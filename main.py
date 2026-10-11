
import os
import time
import asyncio
import threading
import requests

from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, jsonify
from metaapi_cloud_sdk import MetaApi


# =====================================================
# SETTINGS
# =====================================================

URL = "https://biquote.io/api/XAUUSD/ohlc"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN", "").strip()
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID", "").strip()

AUTO_TRADE = os.getenv("AUTO_TRADE", "false").lower() == "true"

SYMBOL = os.getenv("SYMBOL", "XAUUSD")
VOLUME = float(os.getenv("TRADE_VOLUME", "0.01"))

IRAQ_TZ = ZoneInfo("Asia/Baghdad")

app = Flask(__name__)
http = requests.Session()

state_lock = threading.RLock()
telegram_lock = threading.Lock()

# MetaApi is initialized only inside its running event loop.
api = None
meta_loop = None
meta_thread = None
meta_ready = threading.Event()
meta_init_lock = threading.Lock()


# =====================================================
# BOT STATE
# =====================================================

support = None
resistance = None
state = "WAITING_SUPPORT_RESISTANCE"

last_1h_time = None
last_15m_time = None
last_manage_15m_time = None
last_update_id = 0

direction = None
entry = None
stop_loss = None
tp1 = None
tp2 = None
tp1_hit = False

current_trade = None
position_id = None

weekly_results = {
    day: [] for day in
    ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
}

last_report_week = None


# =====================================================
# METAAPI EVENT LOOP
# =====================================================

def start_metaapi_loop():
    global meta_loop, meta_thread

    if not METAAPI_TOKEN:
        raise RuntimeError("METAAPI_TOKEN is missing")

    with meta_init_lock:
        if meta_loop is not None and meta_loop.is_running():
            return

        meta_ready.clear()

        def loop_worker():
            global meta_loop

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            meta_loop = loop
            meta_ready.set()

            print("MetaApi event loop started", flush=True)

            try:
                loop.run_forever()
            finally:
                pending = asyncio.all_tasks(loop)

                for task in pending:
                    task.cancel()

                if pending:
                    loop.run_until_complete(
                        asyncio.gather(
                            *pending,
                            return_exceptions=True
                        )
                    )

                loop.close()

        meta_thread = threading.Thread(
            target=loop_worker,
            name="MetaApiEventLoop",
            daemon=True
        )
        meta_thread.start()

    if not meta_ready.wait(timeout=10):
        raise RuntimeError("MetaApi event loop failed to start")


async def initialize_metaapi():
    global api

    if api is None:
        if not METAAPI_TOKEN:
            raise RuntimeError("METAAPI_TOKEN is missing")

        # This constructor now runs on the active event-loop thread.
        api = MetaApi(METAAPI_TOKEN)

    return api


def run_metaapi(coro, timeout=120):
    try:
        start_metaapi_loop()

        future = asyncio.run_coroutine_threadsafe(
            coro,
            meta_loop
        )

        return future.result(timeout=timeout)

    except Exception:
        # Prevent an unawaited-coroutine warning if scheduling failed.
        if hasattr(coro, "close"):
            coro.close()
        raise


# =====================================================
# TIME / CANDLE HELPERS
# =====================================================

def iraq_now():
    return datetime.now(IRAQ_TZ)


def today_name():
    return iraq_now().strftime("%A")


def is_closed(candle):
    value = candle.get("isOpen")
    return (
        value is False
        or str(value).lower() in ("false", "0")
    )


def candle_time_key(candle):
    value = candle.get("openTime", 0)

    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            ).timestamp()
        except (ValueError, TypeError, OverflowError):
            return 0


def candle_time_id(candle):
    if not candle:
        return None

    value = candle.get("openTime")

    if value is None:
        return candle_time_key(candle)

    return str(value)


def get_bars(interval, limit=30):
    try:
        response = http.get(
            URL,
            params={
                "interval": interval,
                "limit": limit
            },
            timeout=15
        )

        response.raise_for_status()
        data = response.json()

        bars = data.get("bars", [])

        if not isinstance(bars, list):
            print("Market ERROR: bars is not a list", flush=True)
            return []

        return sorted(
            bars,
            key=candle_time_key,
            reverse=True
        )

    except Exception as e:
        print("Market ERROR:", repr(e), flush=True)
        return []


def get_closed_bars(interval, count=2):
    bars = get_bars(interval, 30)

    closed = [
        candle for candle in bars
        if is_closed(candle)
    ]

    return closed[:count]


def get_latest_closed(interval):
    bars = get_closed_bars(interval, 1)
    return bars[0] if bars else None


# =====================================================
# CANDLE PATTERNS
# =====================================================

def is_hammer(o, h, l, c):
    body = abs(c - o)

    if body <= 0:
        return False

    upper = h - max(o, c)
    lower = min(o, c) - l

    return (
        lower >= body * 2
        and upper <= body
    )


def is_shooting_star(o, h, l, c):
    body = abs(c - o)

    if body <= 0:
        return False

    upper = h - max(o, c)
    lower = min(o, c) - l

    return (
        upper >= body * 2
        and lower <= body
    )


def is_bullish_engulfing(po, pc, co, cc):
    return (
        pc < po
        and cc > co
        and co <= pc
        and cc >= po
    )


def is_bearish_engulfing(po, pc, co, cc):
    return (
        pc > po
        and cc < co
        and co >= pc
        and cc <= po
    )


# =====================================================
# TELEGRAM
# =====================================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print(
            "Telegram ERROR: BOT_TOKEN or CHAT_ID missing",
            flush=True
        )
        return False

    try:
        with telegram_lock:
            response = http.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": CHAT_ID,
                    "text": message
                },
                timeout=15
            )

        result = response.json()

        print(
            "Telegram:",
            response.status_code,
            flush=True
        )

        if not response.ok or not result.get("ok"):
            print("Telegram ERROR:", result, flush=True)
            return False

        return True

    except Exception as e:
        print("Telegram ERROR:", repr(e), flush=True)
        return False


# =====================================================
# RESET
# =====================================================

def reset_trade():
    global direction, entry, stop_loss, tp1, tp2
    global tp1_hit, current_trade, position_id
    global last_manage_15m_time

    direction = None
    entry = None
    stop_loss = None
    tp1 = None
    tp2 = None
    tp1_hit = False
    current_trade = None
    position_id = None
    last_manage_15m_time = None


def reset_everything():
    global support, resistance, state
    global last_1h_time, last_15m_time

    with state_lock:
        support = None
        resistance = None
        state = "WAITING_SUPPORT_RESISTANCE"

        last_1h_time = None
        last_15m_time = None

        reset_trade()

    print(
        "RESET: Waiting for new Support / Resistance",
        flush=True
    )


# =====================================================
# METAAPI ORDERS
# =====================================================

async def broker_order(action, volume, sl, tp):
    global api

    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        return False, "MetaApi credentials are missing"

    await initialize_metaapi()

    account = await api.metatrader_account_api.get_account(
        METAAPI_ACCOUNT_ID
    )

    if account.state != "DEPLOYED":
        await account.deploy()

    await account.wait_connected()

    connection = account.get_rpc_connection()

    await connection.connect()
    await connection.wait_synchronized()

    if action == "BUY":
        result = await connection.create_market_buy_order(
            SYMBOL, volume, sl, tp
        )
    else:
        result = await connection.create_market_sell_order(
            SYMBOL, volume, sl, tp
        )

    print("BROKER ORDER RESULT:", result, flush=True)

    pid = None

    if isinstance(result, dict):
        pid = (
            result.get("positionId")
            or result.get("position_id")
        )

        if not pid and isinstance(result.get("position"), dict):
            pid = (
                result["position"].get("id")
                or result["position"].get("positionId")
            )

    if not pid:
        return False, {
            "result": result,
            "error": "Order result did not contain a position ID"
        }

    return True, {
        "result": result,
        "position_id": pid
    }


def place_broker_order(action, volume, sl, tp):
    try:
        return run_metaapi(
            broker_order(action, volume, sl, tp)
        )

    except Exception as e:
        print("MetaApi ORDER ERROR:", repr(e), flush=True)
        return False, str(e)


async def modify_broker_position(pid, new_sl, new_tp):
    global api

    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID or not pid:
        return False

    await initialize_metaapi()

    account = await api.metatrader_account_api.get_account(
        METAAPI_ACCOUNT_ID
    )

    await account.wait_connected()

    connection = account.get_rpc_connection()

    await connection.connect()
    await connection.wait_synchronized()

    await connection.modify_position(
        pid,
        new_sl,
        new_tp
    )

    return True


def move_broker_sl_to_entry():
    if not position_id:
        print(
            "Cannot move broker SL: position ID missing",
            flush=True
        )
        return False

    try:
        return run_metaapi(
            modify_broker_position(
                position_id,
                entry,
                tp2
            )
        )

    except Exception as e:
        print("MetaApi MODIFY ERROR:", repr(e), flush=True)
        return False


# =====================================================
# SUPPORT / RESISTANCE
# =====================================================

def set_support_resistance(new_support, new_resistance):
    global support, resistance, state
    global last_1h_time, last_15m_time

    with state_lock:
        support = new_support
        resistance = new_resistance

        reset_trade()

        candle1 = get_latest_closed("1h")
        candle15 = get_latest_closed("15m")

        last_1h_time = candle_time_id(candle1)
        last_15m_time = candle_time_id(candle15)

        state = "WAITING_1H"

    send_telegram(
        "✅ SUPPORT / RESISTANCE UPDATED\n\n"
        f"🟦 Support: {support:.2f}\n"
        f"🟥 Resistance: {resistance:.2f}\n\n"
        "🤖 Bot is watching XAUUSD."
    )

    print(
        "S/R UPDATED:",
        support,
        resistance,
        flush=True
    )


# =====================================================
# TELEGRAM COMMANDS
# =====================================================

def check_telegram():
    global last_update_id

    if not BOT_TOKEN or not CHAT_ID:
        return

    try:
        response = http.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={
                "offset": last_update_id + 1,
                "timeout": 1
            },
            timeout=5
        )

        response.raise_for_status()
        data = response.json()

        if not data.get("ok"):
            print("Telegram getUpdates ERROR:", data, flush=True)
            return

        for update in data.get("result", []):
            last_update_id = update["update_id"]

            message = update.get("message")

            if not message:
                continue

            text = message.get("text", "").strip()
            chat_id = str(
                message.get("chat", {}).get("id", "")
            )

            if chat_id != str(CHAT_ID):
                continue

            print("Telegram Command:", text, flush=True)

            if text.startswith("/support"):
                parts = text.split()

                if len(parts) != 3:
                    send_telegram(
                        "Use:\n/support 4275 4300"
                    )
                    continue

                try:
                    s = float(parts[1])
                    r = float(parts[2])

                    if s <= 0 or r <= 0 or s >= r:
                        send_telegram(
                            "❌ Invalid prices.\n"
                            "Support must be below Resistance."
                        )
                        continue

                    set_support_resistance(s, r)

                except ValueError:
                    send_telegram(
                        "❌ Invalid prices.\n"
                        "Example:\n/support 4275 4300"
                    )

            elif text == "/cancel":
                reset_everything()

                send_telegram(
                    "❌ SUPPORT / RESISTANCE CANCELLED\n\n"
                    "⏳ Waiting for new Support / Resistance."
                )

            elif text == "/status":
                if support is None:
                    send_telegram(
                        "Waiting for Support / Resistance."
                    )
                else:
                    send_telegram(
                        "📊 XAUUSD BOT STATUS\n\n"
                        f"🟦 Support: {support:.2f}\n"
                        f"🟥 Resistance: {resistance:.2f}\n"
                        f"State: {state}\n"
                        f"Auto trade: {AUTO_TRADE}"
                    )

            elif text == "/start" or text == "/help":
                send_telegram(
                    "🤖 XAUUSD BOT COMMANDS\n\n"
                    "/support 4275 4300\n"
                    "/status\n"
                    "/cancel\n\n"
                    "Send Support and Resistance to start."
                )

    except Exception as e:
        print(
            "Telegram CHECK ERROR:",
            repr(e),
            flush=True
        )


# =====================================================
# 1H BREAKOUT
# =====================================================

def check_1h():
    global state, last_1h_time, last_15m_time

    if support is None or resistance is None:
        return

    candle = get_latest_closed("1h")

    if not candle:
        return

    candle_time = candle_time_id(candle)

    if candle_time == last_1h_time:
        return

    last_1h_time = candle_time

    try:
        close_price = float(candle["close"])
    except (KeyError, TypeError, ValueError):
        print("Invalid 1H candle", flush=True)
        return

    print(
        "NEW CLOSED 1H:",
        close_price,
        "Support:", support,
        "Resistance:", resistance,
        flush=True
    )

    if close_price >= resistance + 5.0:
        state = "WAITING_BUY_15M"

        current = get_latest_closed("15m")

        if current:
            last_15m_time = candle_time_id(current)

        send_telegram(
            "🟢 Confirmation for Today BUY\n\n"
            "Wait for Confirmation Candle BUY 15M."
        )

    elif close_price <= support - 5.0:
        state = "WAITING_SELL_15M"

        current = get_latest_closed("15m")

        if current:
            last_15m_time = candle_time_id(current)

        send_telegram(
            "🔴 Confirmation for Today SELL\n\n"
            "Wait for Confirmation Candle SELL 15M."
        )

    else:
        print("NO 1H BREAKOUT", flush=True)


# =====================================================
# TRADE RECORDS
# =====================================================

def start_trade_record():
    global current_trade

    current_trade = {
        "day": today_name(),
        "profit": 0,
        "stop_loss": 0,
        "status": "OPEN"
    }


def finish_trade(result):
    global current_trade

    if current_trade is None:
        return

    day = current_trade["day"]

    if day in weekly_results:
        weekly_results[day].append(result)

    current_trade = None


# =====================================================
# OPEN TRADE
# =====================================================

def open_trade(action, candle, pattern):
    global direction, entry, stop_loss, tp1, tp2
    global tp1_hit, state, position_id

    o = float(candle["open"])
    h = float(candle["high"])
    l = float(candle["low"])
    c = float(candle["close"])

    direction = action
    entry = c

    if action == "BUY":
        stop_loss = l - 3.0
        tp1 = entry + 10.0
        tp2 = entry + 15.0
        state = "MANAGING_BUY"

    else:
        stop_loss = h + 3.0
        tp1 = entry - 10.0
        tp2 = entry - 15.0
        state = "MANAGING_SELL"

    tp1_hit = False
    position_id = None

    start_trade_record()

    order_text = (
        "Signal only: AUTO_TRADE is disabled."
    )

    if AUTO_TRADE:
        ok, result = place_broker_order(
            action,
            VOLUME,
            stop_loss,
            tp2
        )

        if not ok:
            send_telegram(
                "❌ ORDER FAILED\n\n"
                f"{result}"
            )

            reset_trade()
            state = (
                "WAITING_BUY_15M"
                if action == "BUY"
                else "WAITING_SELL_15M"
            )
            return

        position_id = result.get("position_id")

        order_text = (
            "Broker order accepted; "
            f"position ID: {position_id}"
        )

    send_telegram(
        f"{'🟢 BUY' if action == 'BUY' else '🔴 SELL'} SIGNAL\n\n"
        f"Pattern: {pattern}\n"
        f"Entry: {entry:.2f}\n"
        f"Stop Loss: {stop_loss:.2f}\n"
        f"TP1: {tp1:.2f}\n"
        f"TP2: {tp2:.2f}\n\n"
        f"{order_text}"
    )

    print(
        action,
        pattern,
        entry,
        stop_loss,
        tp1,
        tp2,
        flush=True
    )


# =====================================================
# 15M CONFIRMATION
# =====================================================

def check_confirmation(action):
    global last_15m_time

    candles = get_closed_bars("15m", 2)

    if len(candles) < 2:
        return

    current = candles[0]
    previous = candles[1]

    candle_time = candle_time_id(current)

    if candle_time == last_15m_time:
        return

    last_15m_time = candle_time

    try:
        o = float(current["open"])
        h = float(current["high"])
        l = float(current["low"])
        c = float(current["close"])

        po = float(previous["open"])
        pc = float(previous["close"])

    except (KeyError, TypeError, ValueError):
        print("Invalid 15M candle data", flush=True)
        return

    if action == "BUY":
        hammer = is_hammer(o, h, l, c)
        engulf = is_bullish_engulfing(
            po, pc, o, c
        )

        if hammer:
            open_trade("BUY", current, "Hammer")

        elif engulf:
            open_trade(
                "BUY",
                current,
                "Bullish Engulfing"
            )

    elif action == "SELL":
        star = is_shooting_star(o, h, l, c)
        engulf = is_bearish_engulfing(
            po, pc, o, c
        )

        if star:
            open_trade(
                "SELL",
                current,
                "Shooting Star"
            )

        elif engulf:
            open_trade(
                "SELL",
                current,
                "Bearish Engulfing"
            )


# =====================================================
# TRADE MANAGEMENT
# =====================================================

def manage_trade():
    global tp1_hit, stop_loss, last_manage_15m_time

    if direction not in ("BUY", "SELL"):
        return

    candle = get_latest_closed("15m")

    if not candle:
        return

    candle_time = candle_time_id(candle)

    if candle_time == last_manage_15m_time:
        return

    last_manage_15m_time = candle_time

    try:
        high = float(candle["high"])
        low = float(candle["low"])

    except (KeyError, TypeError, ValueError):
        return

    is_buy = direction == "BUY"

    # If SL and target overlap within one candle,
    # conservatively assume SL was hit first.

    if not tp1_hit:
        stopped = (
            low <= stop_loss
            if is_buy
            else high >= stop_loss
        )

        if stopped:
            distance = abs(entry - stop_loss)
            points = round(distance * 10)

            finish_trade({
                "profit": 0,
                "stop_loss": points
            })

            send_telegram(
                f"⛔ {direction} STOP LOSS\n"
                f"Entry: {entry:.2f}\n"
                f"SL: {stop_loss:.2f}\n"
                "Waiting for new Support / Resistance."
            )

            reset_everything()
            return

        reached_tp1 = (
            high >= tp1
            if is_buy
            else low <= tp1
        )

        if reached_tp1:
            if AUTO_TRADE:
                if not move_broker_sl_to_entry():
                    send_telegram(
                        "⚠️ TP1 reached, but broker SL "
                        "modification was not confirmed. "
                        "Check the open position manually."
                    )
                    return

            tp1_hit = True
            stop_loss = entry

            send_telegram(
                "🎯 TP1 HIT\n\n"
                f"Entry: {entry:.2f}\n"
                f"New SL target: {entry:.2f}\n"
                "Waiting for TP2."
            )

            return

    else:
        returned_to_entry = (
            low <= entry
            if is_buy
            else high >= entry
        )

        if returned_to_entry:
            if AUTO_TRADE:
                send_telegram(
                    "⚠️ Price returned to entry.\n"
                    "Verify the actual broker position "
                    "before recording the trade as closed."
                )
                return

            finish_trade({
                "profit": 100,
                "stop_loss": 0
            })

            send_telegram(
                "⚠️ PRICE RETURNED TO ENTRY\n"
                "Signal tracking ended.\n"
                "Waiting for new Support / Resistance."
            )

            reset_everything()
            return

        reached_tp2 = (
            high >= tp2
            if is_buy
            else low <= tp2
        )

        if reached_tp2:
            if AUTO_TRADE:
                send_telegram(
                    "🎯 TP2 price reached.\n"
                    "Verify the actual broker position "
                    "and closing result before recording "
                    "the trade as closed."
                )
                return

            finish_trade({
                "profit": 150,
                "stop_loss": 0
            })

            send_telegram(
                "🎉 TP2 REACHED\n"
                "Signal tracking ended.\n"
                "Waiting for new Support / Resistance."
            )

            reset_everything()


# =====================================================
# WEEKLY REPORT
# =====================================================

def make_weekly_report():
    total_profit = 0
    total_stop = 0

    lines = ["📊 WEEKLY RESULT"]

    for day, trades in weekly_results.items():
        lines.append(f"\n{day}")

        if not trades:
            lines.append("No recorded trades")
            continue

        for i, trade in enumerate(trades, 1):
            lines.append(f"Trade {i}")
            lines.append(
                f"Profit: {trade['profit']} points"
            )
            lines.append(
                f"Stop Loss: {trade['stop_loss']} points"
            )

            total_profit += trade["profit"]
            total_stop += trade["stop_loss"]

    lines.extend([
        "\nTotal Weekly",
        f"Profit: {total_profit} points",
        f"Stop Loss: {total_stop} points"
    ])

    return "\n".join(lines)


def check_weekly_report():
    global last_report_week, weekly_results

    now = iraq_now()

    # Friday at or after 23:00 Iraq time.
    if now.weekday() != 4 or now.hour < 23:
        return

    week = now.strftime("%Y-%W")

    if week == last_report_week:
        return

    if send_telegram(make_weekly_report()):
        last_report_week = week

        weekly_results = {
            day: [] for day in
            [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday"
            ]
        }


# =====================================================
# FLASK
# =====================================================

@app.route("/", methods=["GET"])
def index():
    return "XAUUSD Bot is running", 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "state": state,
        "auto_trade": AUTO_TRADE,
        "support": support,
        "resistance": resistance,
        "metaapi_loop_running": (
            meta_loop is not None
            and meta_loop.is_running()
        )
    }), 200


# =====================================================
# MAIN LOOP
# =====================================================

def run_bot_loop():
    print("XAUUSD BOT STARTED", flush=True)
    print("Timezone: Asia/Baghdad", flush=True)
    print("AUTO_TRADE:", AUTO_TRADE, flush=True)

    if not BOT_TOKEN or not CHAT_ID:
        print(
            "WARNING: Telegram credentials missing",
            flush=True
        )

    if AUTO_TRADE:
        print(
            "WARNING: AUTO_TRADE is enabled. "
            "Real orders may be submitted.",
            flush=True
        )

        if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
            print(
                "WARNING: MetaApi credentials missing",
                flush=True
            )

    while True:
        try:
            check_telegram()
            check_weekly_report()

            if support is None or resistance is None:
                time.sleep(5)
                continue

            if state == "WAITING_1H":
                check_1h()

            elif state == "WAITING_BUY_15M":
                check_confirmation("BUY")

            elif state == "WAITING_SELL_15M":
                check_confirmation("SELL")

            elif state in (
                "MANAGING_BUY",
                "MANAGING_SELL"
            ):
                manage_trade()

            time.sleep(15)

        except Exception as e:
            print(
                "MAIN LOOP ERROR:",
                repr(e),
                flush=True
            )
            time.sleep(15)


# =====================================================
# START
# =====================================================

if __name__ == "__main__":
    print("Starting XAUUSD Telegram bot...", flush=True)

    threading.Thread(
        target=run_bot_loop,
        name="XAUUSDMonitor",
        daemon=True
    ).start()

    port = int(os.environ.get("PORT", "5000"))

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
