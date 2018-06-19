import time
import logging
import sqlite3
import os

from datetime import datetime

from mas_tools.api import Binance


#=============================================================================#
#   G L O B A L   V A R I A B L E S                                           #
#=============================================================================#
MY_API_KEY = '---'
MY_API_SECRET = '---'
ORDER_RISK = 2 * 0.01
MONTH_RISK = 6 * 0.01

EX_NAME = 'Binance'

## Пропишите пары, на которые будет идти торговля.
#   base - это базовая пара (BTC, ETH,  BNB, USDT) - то, что на бинансе пишется в табличке сверху
#   quote - это квотируемая валюта. Например, для торгов по паре NEO/USDT базовая валюта USDT, NEO - квотируемая
PAIRS = [{
        'base': 'ETH',
        'quote': 'BNB',
        'offers_amount': 5, # Сколько предложений из стакана берем для расчета средней цены
                            # Максимум 1000. Допускаются следующие значения:[5, 10, 20, 50, 100, 500, 1000]
        'spend_sum': 0.95,  # Сколько тратить base каждый раз при покупке quote
        'profit_markup': 0.005, # Какой навар нужен с каждой сделки? (0.001 = 0.1%)
    }, {
        'base': 'ETH',
        'quote': 'EOS',
        'offers_amount': 5, # Сколько предложений из стакана берем для расчета средней цены
                            # Максимум 1000. Допускаются следующие значения:[5, 10, 20, 50, 100, 500, 1000]
        'spend_sum': 0.02,  # Сколько тратить base каждый раз при покупке quote
        'profit_markup': 0.005, # Какой навар нужен с каждой сделки? (0.001 = 0.1%)
    }, {
        'base': 'ETH',
        'quote': 'BCC',
        'offers_amount': 5, # Сколько предложений из стакана берем для расчета средней цены
                            # Максимум 1000. Допускаются следующие значения:[5, 10, 20, 50, 100, 500, 1000]
        'spend_sum': 0.02,  # Сколько тратить base каждый раз при покупке quote
        'profit_markup': 0.005, # Какой навар нужен с каждой сделки? (0.001 = 0.1%)
    }
]

BUY_LIFE_TIME_SEC = 180 # Сколько (в секундах) держать ордер на продажу открытым
STOCK_FEE = 0.001       # Комиссия, которую берет биржа (0.001 = 0.1%)
USE_BNB_FEES = True     # Комиссия в BNB

SLEEP = 0.1
tick_count = 0

DB_NEW_TABLE = """
    create table if not exists
        orders (
        order_type TEXT,
        order_pair TEXT,

        buy_order_id NUMERIC,
        buy_amount REAL,
        buy_price REAL,
        buy_created DATETIME,
        buy_finished DATETIME NULL,
        buy_cancelled DATETIME NULL,

        sell_order_id NUMERIC NULL,
        sell_amount REAL NULL,
        sell_price REAL NULL,
        sell_created DATETIME NULL,
        sell_finished DATETIME NULL
        );"""

DB_SELECT_ORDER = """
    SELECT
        CASE WHEN order_type='buy' THEN buy_order_id ELSE sell_order_id END order_id
        , order_type
        , order_pair
        , sell_amount
        , sell_price
        , strftime('%s',buy_created)
        , buy_amount
        , buy_price
    FROM orders
    WHERE
        buy_cancelled IS NULL AND CASE WHEN order_type='buy' THEN buy_finished IS NULL ELSE sell_finished IS NULL END"""

DB_SELECT_ORDERS = """
    SELECT
        distinct(order_pair) pair
    FROM orders
    WHERE
        buy_cancelled IS NULL AND CASE WHEN order_type='buy' THEN buy_finished IS NULL ELSE sell_finished IS NULL END"""

DB_UPDATE_SELLED_ORDER = """
    UPDATE orders
    SET
        sell_finished = datetime()
    WHERE
        sell_order_id = :sell_order_id"""

DB_UPDATE_OPENED_ORDER = """
    UPDATE orders
    SET
        order_type = 'sell',
        buy_finished = datetime(),
        sell_order_id = :sell_order_id,
        sell_created = datetime(),
        sell_amount = :sell_amount,
        sell_price = :sell_initial_price
    WHERE
        buy_order_id = :buy_order_id"""

DB_UPDATE_CANCELLED_ORDER = """
    UPDATE orders
    SET
        buy_cancelled = datetime()
    WHERE
        buy_order_id = :buy_order_id"""

DB_INSERT_ORDER = """
    INSERT INTO orders(
        order_type,
        order_pair,
        buy_order_id,
        buy_amount,
        buy_price,
        buy_created
    ) Values (
        'buy',
        :order_pair,
        :order_id,
        :buy_order_amount,
        :buy_initial_price,
        datetime()
    )"""

#=============================================================================#
#   F U N C T I O N S                                                         #
#=============================================================================#

# Ф-ция, которая приводит любое число к числу, кратному шагу, указанному биржей
# Если передать параметр increase=True то округление произойдет к следующему шагу
def adjust_to_step(value, step, increase=False):
   return ((int(value * 100000000) - int(value * 100000000) % int(
        float(step) * 100000000)) / 100000000)+(float(step) if increase else 0)


#=============================================================================#
#   P R O G R A M   R U N                                                     #
#=============================================================================#
if __name__ == "__main__":
    #=========================================================================#
    #   I N I T I A L A Z A T I O N                                           #
    #=========================================================================#

    bot = Binance(API_KEY = MY_API_KEY, API_SECRET = MY_API_SECRET)
    # load exchange info
    # init global variables

    # Подключаем логирование
    logging.basicConfig(
        format="%(asctime)s [%(levelname)-5.5s] %(message)s",
        level=logging.DEBUG,
        handlers=[
            logging.FileHandler("{path}/logs/{fname}.log".format(path=os.path.dirname(os.path.abspath(__file__)), fname=EX_NAME)),
            logging.StreamHandler()
        ])
    log = logging.getLogger('')

    # Получаем ограничения торгов по всем парам с биржи
    local_time = int(time.time())
    limits = bot.exchange_info()
    server_time = int(limits['serverTime'])//1000

    # Init time shift, print time info
    shift_seconds = server_time-local_time
    bot.set_shift_seconds(shift_seconds)
    
    log.debug("""
            Текущее время: {local_time_d} {local_time_u}
            Время сервера: {server_time_d} {server_time_u}
            Разница: {diff:0.8f} {warn}
            Бот будет работать, как будто сейчас: {fake_time_d} {fake_time_u}
        """.format(
            local_time_d = datetime.fromtimestamp(local_time), local_time_u=local_time,
            server_time_d=datetime.fromtimestamp(server_time), server_time_u=server_time,
            diff=abs(local_time-server_time),
            warn="ТЕКУЩЕЕ ВРЕМЯ ВЫШЕ" if local_time > server_time else '',
            fake_time_d=datetime.fromtimestamp(local_time+shift_seconds), fake_time_u=local_time+shift_seconds
        ))


    #=========================================================================#
    #   M A I N   L O O P                                                     #
    #=========================================================================#
    while True:
        try:
            if tick_count % 1200 == 0:
                # Получаем ограничения торгов по всем парам с биржи
                # Init time shift, print time info
                bot.set_shift_seconds(int(bot.exchange_info()['serverTime'])//1000 - int(time.time()))

            time.sleep(SLEEP)

            # Устанавливаем соединение с локальной базой данных
            conn = sqlite3.connect(EX_NAME+'.db')
            cursor = conn.cursor()
            
            # формируем словарь из указанных пар, для удобного доступа
            all_pairs = {pair['quote'].upper() + pair['base'].upper():pair for pair in PAIRS}

            # Если не существует таблиц, их нужно создать (первый запуск)
            cursor.execute(DB_NEW_TABLE)
            # Получаем все неисполненные ордера по БД
            log.debug("Получаем все неисполненные ордера по БД")
            orders_info = {}

            for row in cursor.execute(DB_SELECT_ORDER):
                orders_info[str(row[0])] = {'order_type': row[1], 'order_pair': row[2], 'sell_amount': row[3], 'sell_price': row[4],
                                            'buy_created': row[5], 'buy_amount': row[6], 'buy_price': row[7] }

##############################################################
            if orders_info:
                log.debug("Получены неисполненные ордера из БД: {orders}".format(orders=[order for order in orders_info]))

                # Проверяем каждый неисполненный по базе ордер
                for order in orders_info:
                    symbol = orders_info[order]['order_pair']
                    # Получаем по ордеру последнюю информацию по бирже
                    stock_order_data = bot.order_info(symbol=symbol, orderId=order)

                    order_status = stock_order_data['status']
                    if order_status == 'NEW':
                        log.debug('Ордер {order} всё еще не выполнен'.format(order=order))

                    # Если ордер на покупку
                    if orders_info[order]['order_type'] == 'buy':
                        # Если ордер уже исполнен
                        if order_status == 'FILLED':
                            log.info("""
                                    Ордер {order} выполнен, получено {exec_qty:0.8f}.
                                    Создаем ордер на продажу
                                """.format(
                                    order=order, exec_qty=float(stock_order_data['executedQty'])
                                ))

                            # смотрим, какие ограничения есть для создания ордера на продажу
                            for elem in limits['symbols']:
                                if elem['symbol'] == symbol:
                                    CURR_LIMITS = elem
                                    break
                            else:
                                raise Exception("Не удалось найти настройки выбранной пары " + symbol)

                            # Рассчитываем данные для ордера на продажу

                            # Имеющееся кол-во на продажу
                            has_amount = orders_info[order]['buy_amount']*((1-STOCK_FEE) if not USE_BNB_FEES else 1)
                            # Приводим количество на продажу к числу, кратному по ограничению
                            sell_amount = adjust_to_step(has_amount, CURR_LIMITS['filters'][1]['stepSize'])
                            # Рассчитываем минимальную сумму, которую нужно получить, что бы остаться в плюсе
                            need_to_earn = orders_info[order]['buy_amount']*orders_info[order]['buy_price']*(1+all_pairs[stock_order_data['symbol']]['profit_markup'])
                            # Рассчитываем минимальную цену для продажи
                            min_price = (need_to_earn/sell_amount)/((1-STOCK_FEE) if not USE_BNB_FEES else 1)
                            # Приводим к нужному виду, если цена после срезки лишних символов меньше нужной, увеличиваем на шаг
                            cut_price = max(
                                adjust_to_step(min_price, CURR_LIMITS['filters'][0]['tickSize'], increase=True),
                                adjust_to_step(min_price, CURR_LIMITS['filters'][0]['tickSize'])
                            )
                            # Получаем текущие курсы с биржи
                            curr_rate = float(bot.ticker_price(symbol=symbol)['price'])
                            # Если текущая цена выше нужной, продаем по текущей
                            need_price = max(cut_price, curr_rate)

                            log.info("""
                                    Изначально было куплено {buy_initial:0.8f}, за вычетом комиссии {has_amount:0.8f},
                                    Получится продать только {sell_amount:0.8f}
                                    Нужно получить как минимум {need_to_earn:0.8f} {curr}
                                    Мин. цена (с комиссией) составит {min_price}, после приведения {cut_price:0.8f}
                                    Текущая цена рынка {curr_rate:0.8f}
                                    Итоговая цена продажи: {need_price:0.8f}
                                """.format(
                                    buy_initial=orders_info[order]['buy_amount'], has_amount=has_amount,sell_amount=sell_amount,
                                    need_to_earn=need_to_earn, curr=all_pairs[symbol]['base'],
                                    min_price=min_price, cut_price=cut_price, need_price=need_price,
                                    curr_rate=curr_rate
                                ))

                            # Если итоговая сумма продажи меньше минимума, ругаемся и не продаем
                            if (need_price*has_amount) <float(CURR_LIMITS['filters'][2]['minNotional']):
                                raise Exception("""Итоговый размер сделки {trade_am:0.8f} меньше допустимого по паре {min_am:0.8f}. """.format(
                                    trade_am=(need_price*has_amount), min_am=float(CURR_LIMITS['filters'][2]['minNotional'])
                                ))

                            log.debug(
                                    'Рассчитан ордер на продажу: кол-во {amount:0.8f}, курс: {rate:0.8f}'.format(
                                        amount=sell_amount, rate=need_price)
                                )

                            # Отправляем команду на создание ордера с рассчитанными параметрами
                            new_order = bot.new_order(
                                symbol=symbol,
                                recvWindow=5000,
                                side='SELL',
                                type='LIMIT',
                                timeInForce='GTC',  # Good Till Cancel
                                quantity="{quantity:0.{precision}f}".format(
                                    quantity=sell_amount, precision=CURR_LIMITS['baseAssetPrecision']
                                ),
                                price="{price:0.{precision}f}".format(
                                    price=need_price, precision=CURR_LIMITS['baseAssetPrecision']
                                ),
                                newOrderRespType='FULL'
                            )
                            # Если ордер создался без ошибок, записываем данные в базу данных
                            if 'orderId' in new_order:
                                log.info("Создан ордер на продажу {new_order}".format(new_order=new_order))
                                cursor.execute(DB_UPDATE_OPENED_ORDER, {'buy_order_id': order,
                                                                        'sell_order_id': new_order['orderId'],
                                                                        'sell_amount': sell_amount,
                                                                        'sell_initial_price': need_price
                                                                    })
                                conn.commit()
                            # Если были ошибки при создании, выводим сообщение
                            else:
                                log.warning("Не удалось создать ордер на продажу {new_order}".format(new_order=new_order))

                        # Ордер еще не исполнен, частичного исполнения нет, проверяем возможность отмены
                        elif order_status == 'NEW':
                            order_created = int(orders_info[order]['buy_created'])
                            time_passed = int(time.time()) - order_created
                            log.info("passed {passed:0.2f}".format(passed=time_passed))
                            # Прошло больше времени, чем разрешено держать ордер
                            if time_passed > BUY_LIFE_TIME_SEC:
                                log.info("""Ордер {order} пора отменять, прошло {passed:0.1f} сек.""".format(
                                        order=order, passed=time_passed
                                    ))
                                # Отменяем ордер на бирже
                                cancel = bot.cancel_order(
                                    symbol=symbol,
                                    orderId=order
                                )
                                # Если удалось отменить ордер, скидываем информацию в БД
                                if 'orderId' in cancel:
                                    log.info("Ордер {order} был успешно отменен".format(order=order))
                                    cursor.execute(DB_UPDATE_CANCELLED_ORDER, {'buy_order_id': order})
                                    conn.commit()
                                else:
                                    log.warning("Не удалось отменить ордер: {cancel}".format(cancel=cancel))
                        elif order_status == 'PARTIALLY_FILLED':
                            log.debug("Ордер {order} частично исполнен, ждем завершения".format(order=order))

                    # Если это ордер на продажу, и он исполнен
                    if order_status == 'FILLED' and orders_info[order]['order_type'] == 'sell':
                        log.debug("Ордер {order} на продажу исполнен".format(
                                order=order
                            ))
                        # Обновляем информацию в БД
                        cursor.execute(DB_UPDATE_SELLED_ORDER, {'sell_order_id': order})
                        conn.commit()

            else:
                log.debug("Неисполненных ордеров в БД нет")

            log.debug('Получаем из настроек все пары, по которым нет неисполненных ордеров')

            # Получаем из базы все ордера, по которым есть торги, и исключаем их из списка, по которому будем создавать новые ордера
            for row in cursor.execute(DB_SELECT_ORDERS):
                del all_pairs[row[0]]

            # Если остались пары, по которым нет текущих торгов
            if all_pairs:
                log.debug('Найдены пары, по которым нет неисполненных ордеров: {pairs}'.format(pairs=list(all_pairs.keys())))
                for pair_name, pair_obj in all_pairs.items():
                    log.debug("Работаем с парой {pair}".format(pair=pair_name))

                    # Получаем лимиты пары с биржи
                    for elem in limits['symbols']:
                        if elem['symbol'] == pair_name:
                            CURR_LIMITS = elem
                            break
                    else:
                        raise Exception("Не удалось найти настройки выбранной пары " + pair_name)

                    # Получаем балансы с биржи по указанным валютам
                    balances = {
                        balance['asset']: float(balance['free']) for balance in bot.account()['balances']
                        if balance['asset'] in [pair_obj['base'], pair_obj['quote']]
                    }
                    log.debug("Баланс {balance}".format(balance=["{k}:{bal:0.8f}".format(k=k, bal=balances[k]) for k in balances]))
                    # Если баланс позволяет торговать - выше лимитов биржи и выше указанной суммы в настройках
                    if balances[pair_obj['base']] >= pair_obj['spend_sum']:
                        # Получаем информацию по предложениям из стакана, в кол-ве указанном в настройках
                        offers = bot.tickers(
                            symbol=pair_name,
                            limit=pair_obj['offers_amount']
                        )

                        # Берем цены покупок (для цен продаж замените bids на asks)
                        prices = [float(bid[0]) for bid in offers['bids']]

                        try:
                            # Рассчитываем среднюю цену из полученных цен
                            avg_price = sum(prices) / len(prices)
                            # Среднюю цену приводим к требованиям биржи о кратности
                            my_need_price = adjust_to_step(avg_price, CURR_LIMITS['filters'][0]['tickSize'])
                            # Рассчитываем кол-во, которое можно купить, и тоже приводим его к кратному значению
                            my_amount = adjust_to_step(pair_obj['spend_sum']/ my_need_price, CURR_LIMITS['filters'][1]['stepSize'])
                            # Если в итоге получается объем торгов меньше минимально разрешенного, то ругаемся и не создаем ордер
                            if my_amount < float(CURR_LIMITS['filters'][1]['stepSize']) or my_amount < float(CURR_LIMITS['filters'][1]['minQty']):
                                log.warning("""
                                        Минимальная сумма лота: {min_lot:0.8f}
                                        Минимальный шаг лота: {min_lot_step:0.8f}
                                        На свои деньги мы могли бы купить {wanted_amount:0.8f}
                                        После приведения к минимальному шагу мы можем купить {my_amount:0.8f}
                                        Покупка невозможна, выход. Увеличьте размер ставки
                                    """.format(
                                        wanted_amount=pair_obj['spend_sum']/ my_need_price,
                                        my_amount=my_amount,
                                        min_lot=float(CURR_LIMITS['filters'][1]['minQty']),
                                        min_lot_step=float(CURR_LIMITS['filters'][1]['stepSize'])
                                    ))
                                continue

                            # Итоговый размер лота
                            trade_am = my_need_price*my_amount
                            log.debug("""
                                    Средняя цена {av_price:0.8f}, 
                                    после приведения {need_price:0.8f}, 
                                    объем после приведения {my_amount:0.8f},
                                    итоговый размер сделки {trade_am:0.8f}
                                    """.format(
                                av_price=avg_price, need_price=my_need_price, my_amount=my_amount, trade_am=trade_am
                            ))
                            # Если итоговый размер лота меньше минимального разрешенного, то ругаемся и не создаем ордер
                            if trade_am < float(CURR_LIMITS['filters'][2]['minNotional']):
                                raise Exception("""
                                        Итоговый размер сделки {trade_am:0.8f} меньше допустимого по паре {min_am:0.8f}. 
                                        Увеличьте сумму торгов (в {incr} раз(а))""".format(
                                        trade_am=trade_am, min_am=float(CURR_LIMITS['filters'][2]['minNotional']),
                                        incr=float(CURR_LIMITS['filters'][2]['minNotional'])/trade_am
                                    ))
                            log.debug(
                                    'Рассчитан ордер на покупку: кол-во {amount:0.8f}, курс: {rate:0.8f}'.format(amount=my_amount, rate=my_need_price)
                                )
                            # Отправляем команду на бирже о создании ордера на покупку с рассчитанными параметрами
                            new_order = bot.new_order(
                                symbol=pair_name,
                                recvWindow=5000,
                                side='BUY',
                                type='LIMIT',
                                timeInForce='GTC',  # Good Till Cancel
                                quantity="{quantity:0.{precision}f}".format(
                                    quantity=my_amount, precision=CURR_LIMITS['baseAssetPrecision']
                                ),
                                price="{price:0.{precision}f}".format(
                                    price=my_need_price, precision=CURR_LIMITS['baseAssetPrecision']
                                ),
                                newOrderRespType='FULL'
                            )
                            # Если удалось создать ордер на покупку, записываем информацию в БД
                            if 'orderId' in new_order:
                                log.info("Создан ордер на покупку {new_order}".format(new_order=new_order))
                                cursor.execute(DB_INSERT_ORDER, {'order_pair': pair_name,
                                                                    'order_id': new_order['orderId'],
                                                                    'buy_order_amount': my_amount,
                                                                    'buy_initial_price': my_need_price
                                                                }
                                                )
                                conn.commit()
                            else:
                                log.warning("Не удалось создать ордер на покупку! {new_order}".format(new_order=str(new_order)))

                        except ZeroDivisionError:
                            log.debug('Не удается вычислить среднюю цену: {prices}'.format(prices=str(prices)))
                    else:
                        log.warning('Для создания ордера на покупку нужно минимум {min_qty:0.8f} {curr}, выход'.format(
                            min_qty=pair_obj['spend_sum'], curr=pair_obj['base']
                        ))
            else:
                log.debug('По всем парам есть неисполненные ордера')
##############################################################

        except Exception as e:
            log.exception(e)

        finally:
            conn.close()


