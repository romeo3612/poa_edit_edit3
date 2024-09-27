from collections import deque
from fastapi.exception_handlers import request_validation_exception_handler
from pprint import pprint
from fastapi import FastAPI, Request, status, BackgroundTasks
from fastapi.responses import ORJSONResponse, RedirectResponse
from fastapi.exceptions import RequestValidationError
import httpx
from exchange.stock.kis import KoreaInvestment
from exchange.pocket import delete_old_records
from exchange.model import MarketOrder, PriceRequest, HedgeData, OrderRequest
from exchange.utility import (
    settings,
    log_order_message,
    log_alert_message,
    print_alert_message,
    logger_test,
    log_order_error_message,
    log_validation_error_message,
    log_hedge_message,
    log_error_message,
    log_message,
)
import traceback
import time
from exchange import get_exchange, log_message, db, settings, get_bot, pocket
import ipaddress
import os
import sys
from devtools import debug
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime  

VERSION = "1.1.6"
app = FastAPI(default_response_class=ORJSONResponse)


ongoing_pairs = {}
order_queues = {}

def get_error(e):
    tb = traceback.extract_tb(e.__traceback__)
    target_folder = os.path.abspath(os.path.dirname(tb[0].filename))  
    error_msg = []

    for tb_info in tb:
        error_msg.append(
            f"File {tb_info.filename}, line {tb_info.lineno}, in {tb_info.name}"
        )
        error_msg.append(f"  {tb_info.line}")

    error_msg.append(str(e))

    return error_msg


@app.on_event("startup")
async def startup():
    log_message(f"POABOT 실행 완료! - 버전:{VERSION}")
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(delete_old_records, 'cron', hour=7, minute=47)  
    scheduler.start()
    print("Scheduler started")

@app.on_event("shutdown")
async def shutdown():
    db.close()

whitelist = [
    "52.89.214.238",
    "34.212.75.30",
    "54.218.53.128",
    "52.32.178.7",
    "127.0.0.1",
]
whitelist = whitelist + settings.WHITELIST

@app.middleware("http")
async def whitelist_middleware(request: Request, call_next):
    try:
        if (
            request.client.host not in whitelist
            and not ipaddress.ip_address(request.client.host).is_private
        ):
            msg = f"{request.client.host}는 안됩니다"
            print(msg)
            return ORJSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": f"{request.client.host}는 허용되지 않습니다"},
            )
    except Exception as e:
        log_error_message(traceback.format_exc(), "미들웨어 에러")
    else:
        response = await call_next(request)
        return response

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    msgs = [
        f"[에러{index+1}] " + f"{error.get('msg')} \n{error.get('loc')}"
        for index, error in enumerate(exc.errors())
    ]
    message = "[Error]\n"
    for msg in msgs:
        message = message + msg + "\n"

    log_validation_error_message(f"{message}\n {exc.body}")
    return await request_validation_exception_handler(request, exc)

@app.get("/ip")
async def get_ip():
    try:
        data = httpx.get("https://ipv4.jsonip.com").json()["ip"]
        log_message(f"IP 조회: {data}")
        return {"ip": data}
    except Exception as e:
        log_error_message(f"IP 조회 중 오류 발생: {str(e)}", {})
        return {"error": "IP 조회 중 오류가 발생했습니다."}

@app.get("/hi")
async def welcome():
    return "hi!!"

@app.post("/price")
async def price(price_req: PriceRequest, background_tasks: BackgroundTasks):
    try:
        exchange = get_exchange(price_req.exchange)
        price = exchange.dict()[price_req.exchange].fetch_price(
            price_req.base, price_req.quote
        )
        log_message(f"가격 조회: {price_req.base}/{price_req.quote} = {price}")
        return {"price": price}
    except Exception as e:
        error_msg = get_error(e)
        log_error_message("\n".join(error_msg), {})
        return {"error": "가격 조회 중 오류가 발생했습니다."}

def log(exchange_name, result, order_info):
    log_order_message(exchange_name, result, order_info)
    print_alert_message(order_info)

def log_error(error_message, order_info):
    log_order_error_message(error_message, order_info)
    log_alert_message(order_info, "실패")


def wait_for_pair_sell_completion(
    exchange_name: str,
    order_info: MarketOrder,
    kis_number: int,
    exchange_instance: KoreaInvestment,
    initial_holding_qty: int,  
    holding_price: float  
):
    try:
        pair = order_info.pair
        total_sell_amount = 0.0
        total_sell_value = 0.0  

        if initial_holding_qty > 0:
            time.sleep(0.5)
            sell_result = exchange_instance.create_order(
                exchange=exchange_name,
                ticker=pair,
                order_type="market",
                side="sell",
                amount=initial_holding_qty,
            )
            
            total_sell_amount += initial_holding_qty
            total_sell_value += initial_holding_qty * holding_price

        for attempt in range(10):
            time.sleep(2)
            holding_qty, holding_price = exchange_instance.fetch_balance_and_price(exchange_name, pair)

            if holding_qty <= 0:
                break

            time.sleep(0.5)
            sell_result = exchange_instance.create_order(
                exchange=exchange_name,
                ticker=pair,
                order_type="market",
                side="sell",
                amount=holding_qty,
            )
            total_sell_amount += holding_qty
            total_sell_value += holding_qty * holding_price

        if holding_qty > 0:
            raise Exception(f"Balance Sell Failed")

        if total_sell_amount > 0:
            record_data = {
                "pair_id": order_info.pair_id,
                "amount": total_sell_amount,
                "value": total_sell_value,
                "ticker": pair,
                "exchange": exchange_name,
                "timestamp": datetime.now().isoformat(),
                "trade_type": "sell"
            }
            response = pocket.create("pair_order_history", record_data)
        return {"status": "success", "total_sell_amount": total_sell_amount, "total_sell_value": total_sell_value}

    except Exception as e:
        error_msg = get_error(e)
        log_error("\n".join(error_msg), order_info)
        return {"status": "error", "error_msg": str(e)}
    finally:
        ongoing_pairs.pop(pair, None)

@app.post("/order")
@app.post("/")
async def order(order_info: MarketOrder, background_tasks: BackgroundTasks):
    exchange_name = order_info.exchange
    pair = order_info.pair
    pair_id = order_info.pair_id
    bot = get_bot(exchange_name, order_info.kis_number)
    bot.init_info(order_info)

    try:
        if pair and pair_id:
            if pair not in order_queues:
                order_queues[pair] = deque()

            if pair in ongoing_pairs:
                order_queues[pair].append(order_info)
                return {"status": "queued", "message" : "Added Queues"}

            ongoing_pairs[pair] = True
            order_queues[pair].append(order_info)

            try:
                while order_queues[pair]:
                    current_order = order_queues[pair].popleft()

                    if current_order.side == "buy":
                        holding_qty, holding_price = bot.fetch_balance_and_price(exchange_name, pair)
                        if holding_qty > 0:
                            wait_for_pair_sell_completion(exchange_name, current_order, current_order.kis_number, bot, holding_qty, holding_price)

                        records = pocket.get_full_list(
                            "pair_order_history",
                            query_params = {
                                "filter": f'pair_id = "{pair_id}" && trade_type = "sell"',
                                "sort": "-timestamp",
                                "limit": 1
                            }
                        )


                        if records:
                            last_sell_record = records[0]
                            total_sell_value = last_sell_record.value
                            adjusted_value = total_sell_value * 0.995  
                            price = order_info.price  
                            buy_amount = int(adjusted_value // price)  


                            if buy_amount > 0:
                                time.sleep(0.5)
                                buy_result = bot.create_order(
                                    bot.order_info.exchange,
                                    bot.order_info.base,
                                    "market",
                                    "buy",
                                    buy_amount,
                                )
                                background_tasks.add_task(log, exchange_name, buy_result, current_order)
                            else:
                                msg = "The calculated purchase quantity is 0"
                                print(f"DEBUG: {msg}")
                                background_tasks.add_task(log_error, msg, current_order)
                        else:

                            time.sleep(0.5)
                            buy_result = bot.create_order(
                                bot.order_info.exchange,
                                bot.order_info.base,
                                "market",
                                "buy",
                                int(current_order.amount),  
                            )
                            background_tasks.add_task(log, exchange_name, buy_result, current_order)

                    elif current_order.side == "sell":
                        holding_qty, holding_price = bot.fetch_balance_and_price(exchange_name, current_order.base)
                        if holding_qty > 0:
                            time.sleep(0.5)
                            sell_result = bot.create_order(
                                bot.order_info.exchange,
                                bot.order_info.base,
                                "market",
                                "sell",
                                holding_qty,
                            )
                            sell_amount = holding_qty
                            sell_value = sell_amount * holding_price
                            record_data = {
                                "pair_id": current_order.pair_id,
                                "amount": sell_amount,
                                "value": sell_value,
                                "ticker": current_order.base,
                                "exchange": exchange_name,
                                "timestamp": datetime.now().isoformat(),
                                "trade_type": "sell"
                            }
                            pocket.create("pair_order_history", record_data)
                            background_tasks.add_task(log, exchange_name, sell_result, current_order)
                        else:
                            msg = "잔고가 존재하지 않습니다"
                            print(f"DEBUG: {msg}")
                            background_tasks.add_task(log_error, msg, current_order)

            except Exception as e:
                error_msg = get_error(e)
                background_tasks.add_task(log_error, "\n".join(error_msg), order_info)
            finally:
                ongoing_pairs.pop(pair, None)
                if not order_queues[pair]:
                    del order_queues[pair]
            return {"status": "success", "message": "주문 처리 완료"}

        else:
            order_result = bot.create_order(
                bot.order_info.exchange,
                bot.order_info.base,
                order_info.type.lower(),
                order_info.side.lower(),
                int(order_info.amount),
            )
            background_tasks.add_task(log, exchange_name, order_result, order_info)

    except Exception as e:
        error_msg = get_error(e)
        print(f"DEBUG: 주문 처리 중 예외 발생 - {error_msg}")
        background_tasks.add_task(log_error, "\n".join(error_msg), order_info)

    return {"status": "success", "message": "주문 처리 완료"}


def get_hedge_records(base):
    records = pocket.get_full_list("kimp", query_params={"filter": f'base = "{base}"'})
    binance_amount = 0.0
    binance_records_id = []
    upbit_amount = 0.0
    upbit_records_id = []
    for record in records:
        if record.exchange == "BINANCE":
            binance_amount += record.amount
            binance_records_id.append(record.id)
        elif record.exchange == "UPBIT":
            upbit_amount += record.amount
            upbit_records_id.append(record.id)

    return {
        "BINANCE": {"amount": binance_amount, "records_id": binance_records_id},
        "UPBIT": {"amount": upbit_amount, "records_id": upbit_records_id},
    }

@app.post("/hedge")
async def hedge(hedge_data: HedgeData, background_tasks: BackgroundTasks):
    exchange_name = hedge_data.exchange.upper()
    bot = get_bot(exchange_name)
    upbit = get_bot("UPBIT")

    base = hedge_data.base
    quote = hedge_data.quote
    amount = hedge_data.amount
    leverage = hedge_data.leverage
    hedge = hedge_data.hedge

    foreign_order_info = OrderRequest(
        exchange=exchange_name,
        base=base,
        quote=quote,
        side="entry/sell",
        type="market",
        amount=amount,
        leverage=leverage,
    )
    bot.init_info(foreign_order_info)
    if hedge == "ON":
        try:
            if amount is None:
                raise Exception("헷지할 수량을 요청하세요")
            binance_order_result = bot.market_entry(foreign_order_info)
            binance_order_amount = binance_order_result["amount"]
            pocket.create(
                "kimp",
                {
                    "exchange": "BINANCE",
                    "base": base,
                    "quote": quote,
                    "amount": binance_order_amount,
                },
            )
            if leverage is None:
                leverage = 1
            try:
                korea_order_info = OrderRequest(
                    exchange="UPBIT",
                    base=base,
                    quote="KRW",
                    side="buy",
                    type="market",
                    amount=binance_order_amount,
                )
                upbit.init_info(korea_order_info)
                upbit_order_result = upbit.market_buy(korea_order_info)
            except Exception as e:
                hedge_records = get_hedge_records(base)
                binance_records_id = hedge_records["BINANCE"]["records_id"]
                binance_amount = hedge_records["BINANCE"]["amount"]
                binance_order_result = bot.market_close(
                    OrderRequest(
                        exchange=exchange_name,
                        base=base,
                        quote=quote,
                        side="close/buy",
                        amount=binance_amount,
                    )
                )
                for binance_record_id in binance_records_id:
                    pocket.delete("kimp", binance_record_id)
                log_message(
                    "[헷지 실패] 업비트에서 에러가 발생하여 바이낸스 포지션을 종료합니다"
                )
            else:
                upbit_order_info = upbit.get_order(upbit_order_result["id"])
                upbit_order_amount = upbit_order_info["filled"]
                pocket.create(
                    "kimp",
                    {
                        "exchange": "UPBIT",
                        "base": base,
                        "quote": "KRW",
                        "amount": upbit_order_amount,
                    },
                )
                log_hedge_message(
                    exchange_name,
                    base,
                    quote,
                    binance_order_amount,
                    upbit_order_amount,
                    hedge,
                )

        except Exception as e:
            background_tasks.add_task(
                log_error_message, traceback.format_exc(), "헷지 에러"
            )
            return {"result": "error"}
        else:
            return {"result": "success"}

    elif hedge == "OFF":
        try:
            records = pocket.get_full_list(
                "kimp", query_params={"filter": f'base = "{base}"'}
            )
            binance_amount = 0.0
            binance_records_id = []
            upbit_amount = 0.0
            upbit_records_id = []
            for record in records:
                if record.exchange == "BINANCE":
                    binance_amount += record.amount
                    binance_records_id.append(record.id)
                elif record.exchange == "UPBIT":
                    upbit_amount += record.amount
                    upbit_records_id.append(record.id)

            if binance_amount > 0 and upbit_amount > 0:
                # 바이낸스
                order_info = OrderRequest(
                    exchange="BINANCE",
                    base=base,
                    quote=quote,
                    side="close/buy",
                    amount=binance_amount,
                )
                binance_order_result = bot.market_close(order_info)
                for binance_record_id in binance_records_id:
                    pocket.delete("kimp", binance_record_id)
                # 업비트
                order_info = OrderRequest(
                    exchange="UPBIT",
                    base=base,
                    quote="KRW",
                    side="sell",
                    amount=upbit_amount,
                )
                upbit_order_result = upbit.market_sell(order_info)
                for upbit_record_id in upbit_records_id:
                    pocket.delete("kimp", upbit_record_id)

                log_hedge_message(
                    exchange_name, base, quote, binance_amount, upbit_amount, hedge
                )
            elif binance_amount == 0 and upbit_amount == 0:
                log_message(f"{exchange_name}, UPBIT에 종료할 수량이 없습니다")
            elif binance_amount == 0:
                log_message(f"{exchange_name}에 종료할 수량이 없습니다")
            elif upbit_amount == 0:
                log_message("UPBIT에 종료할 수량이 없습니다")
        except Exception as e:
            background_tasks.add_task(
                log_error_message, traceback.format_exc(), "헷지종료 에러"
            )
            return {"result": "error"}
        else:
            return {"result": "success"}
