from collections import deque
from fastapi.exception_handlers import request_validation_exception_handler
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
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime

VERSION = "1.2.1"
app = FastAPI(default_response_class=ORJSONResponse)

# 글로벌 딕셔너리 및 큐 추가 (페어 진행 상태 및 큐 관리)
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
    
    # APScheduler 스케줄러 시작
    scheduler = BackgroundScheduler()
    scheduler.add_job(delete_old_records, 'cron', hour=7, minute=47)  # 매일 오전 7시 47분에 실행
    scheduler.start()

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

def execute_split_order(
    exchange_instance: KoreaInvestment,
    exchange_name: str,
    ticker: str,
    order_type: str,
    side: str,
    total_amount: int,
    background_tasks: BackgroundTasks,
    order_info: MarketOrder,
):
    if exchange_name == "KRX":
        delay_time = 0.0
    elif exchange_name in ["NASDAQ", "NYSE", "AMEX"]:
        delay_time = 0.0
    else:
        try:
            order_result = exchange_instance.create_order(
                exchange=exchange_name,
                ticker=ticker,
                order_type=order_type,
                side=side,
                amount=total_amount,
            )
        except Exception as e:
            raise e
        return

    first_order_price = order_info.price

    split_amount = total_amount // 10
    remaining_amount = total_amount % 10

    error_occurred = False

    for i in range(10):
        order_qty = split_amount + (1 if i < remaining_amount else 0)

        if order_qty <= 0:
            continue

        try:
            order_result = exchange_instance.create_order(
                exchange=exchange_name,
                ticker=ticker,
                order_type=order_type,
                side=side,
                amount=order_qty,
                price=first_order_price
            )

        except Exception as e:
            raise e

        time.sleep(delay_time)

def wait_for_pair_sell_completion(
    exchange_name: str,
    order_info: MarketOrder,
    kis_number: int,
    exchange_instance: KoreaInvestment,
    initial_holding_qty: int,
    holding_price: float,
    background_tasks: BackgroundTasks,
):
    try:
        pair = order_info.pair

        total_sell_amount = 0.0
        total_sell_value = 0.0

        if initial_holding_qty > 0:
            execute_split_order(
                exchange_instance,
                exchange_name,
                pair,
                "market",
                "sell",
                initial_holding_qty,
                background_tasks,
                order_info,
            )
            total_sell_amount += initial_holding_qty
            total_sell_value += initial_holding_qty * holding_price

        for attempt in range(10):
            time.sleep(4)
            try:
                holding_qty, holding_price = exchange_instance.fetch_balance_and_price(exchange_name, pair)
            except Exception as e:
                raise e

            if holding_qty <= 0:
                break

            execute_split_order(
                exchange_instance,
                exchange_name,
                pair,
                "market",
                "sell",
                holding_qty,
                background_tasks,
                order_info,
            )
            total_sell_amount += holding_qty
            total_sell_value += holding_qty * holding_price

        if holding_qty > 0:
            raise Exception(f"{attempt + 1}회 시도 후 잔고 남음: {holding_qty}")

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
        raise e
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
                return {"status": "queued", "message": f"{pair} 주문이 큐에 추가되었습니다."}

            ongoing_pairs[pair] = True
            order_queues[pair].append(order_info)

            try:
                while order_queues[pair]:
                    current_order = order_queues[pair].popleft()

                    if current_order.side == "buy":
                        holding_qty, holding_price = bot.fetch_balance_and_price(exchange_name, pair)
                        if holding_qty > 0:
                            wait_for_pair_sell_completion(
                                exchange_name, current_order, current_order.kis_number, bot, holding_qty, holding_price, background_tasks
                            )

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

                            adjusted_value = total_sell_value * 0.9985
                            price = order_info.price
                            buy_amount = int(adjusted_value // price)

                            if buy_amount > 0:
                                execute_split_order(
                                    bot, exchange_name, current_order.base, "market", "buy", buy_amount, background_tasks, current_order
                                )
                                background_tasks.add_task(log, exchange_name, "매수 완료", current_order)
                            else:
                                msg = "계산된 매수 수량이 0입니다."
                                background_tasks.add_task(log_error, msg, current_order)
                        else:
                            execute_split_order(
                                bot, exchange_name, current_order.base, "market", "buy", int(current_order.amount), background_tasks, current_order
                            )
                            background_tasks.add_task(log, exchange_name, "매수 완료", current_order)

                    elif current_order.side == "sell":
                        holding_qty, holding_price = bot.fetch_balance_and_price(exchange_name, current_order.base)
                        if holding_qty > 0:
                            execute_split_order(
                                bot, exchange_name, current_order.base, "market", "sell", holding_qty, background_tasks, current_order
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
                            background_tasks.add_task(log, exchange_name, "매도 완료", current_order)
                        else:
                            msg = "잔고가 존재하지 않습니다"
                            background_tasks.add_task(log_error, msg, current_order)

            except Exception as e:
                error_msg = get_error(e)
                background_tasks.add_task(log_error, "\n".join(error_msg), order_info)
                return {"status": "error", "message": "주문 처리 중 오류가 발생했습니다.", "error": str(e)}
            finally:
                ongoing_pairs.pop(pair, None)
                if not order_queues[pair]:
                    del order_queues[pair]
            return {"status": "success", "message": "주문 처리 완료"}

        else:
            execute_split_order(
                bot, exchange_name, order_info.base, order_info.type.lower(), order_info.side.lower(), int(order_info.amount), background_tasks, order_info
            )
            background_tasks.add_task(log, exchange_name, "주문 완료", order_info)
            return {"status": "success", "message": "주문 처리 완료"}

    except Exception as e:
        error_msg = get_error(e)
        background_tasks.add_task(log_error, "\n".join(error_msg), order_info)
        return {"status": "error", "message": "주문 처리 중 오류가 발생했습니다.", "error": str(e)}

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
