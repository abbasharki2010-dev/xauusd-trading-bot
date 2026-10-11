
import os
import time
import asyncio
import threading
import requests

from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask
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

api = MetaApi(METAAPI_TOKEN) if METAAPI_TOKEN else None

http = requests.Session()
state_lock = threading.RLock()


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
# TIME / CANDLE HELPERS
# =====================================================

def iraq_now():
    return datetime.now(IRAQ_TZ)


def today_name():
    return iraq_now().strftime("%A")


def is_closed(candle):
    value = candle.get("isOpen")
    return value is False or str(value).lower() in ("false", "0")


def candle_time_key(candle):
    value = candle.get("openTime", 0)

    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            ).timestamp()
        except (ValueError, TypeError):
            return 0


def get_bars(interval, limit=20):
    try:
        response = http.get(
            URL,
            params={"interval": interval, "limit": limit},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        bars = data.get("bars", [])

        if not isinstance(bars, list):
            print("Market ERROR: bars is not a list")
            return []

        return sorted(bars, key=candle_time_key, reverse=True)

    except Exception as e:
        print("Market ERROR:", str(e), flush=True)
        return []


def get_closed_bars(interval, count=2):
    bars = get_bars(interval, 30)
    closed = [c for c in bars if is_closed(c)]
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

    return lower >= body * 2 and upper <= body


def is_shooting_star(o, h, l, c):
    body = abs(c - o)
    if body <= 0:
        return False

    upper = h - max(o, c)
    lower = min(o, c) - l

    return upper >= body * 2 and lower <= body


def is_bullish_engulfing(po, pc, co, cc):
    return (
        pc < po and cc > co
        and co <= pc and cc >= po
    )


def is_bearish_engulfing(po, pc, co, cc):
    return (
        pc > po and cc < co
        and co >= pc and cc <= po
    )


# =====================================================
# TELEGRAM
# =====================================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram ERROR: BOT_TOKEN or CHAT_ID missing")
        return False

    try:
        response = http.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message},
            timeout=15
        )

        result = response.json()

        print("Telegram:", response.status_code, flush=True)

        if not response.ok or not result.get("ok"):
            print("Telegram ERROR:", result)
            return False

        return True

    except Exception as e:
        print("Telegram ERROR:", str(e), flush=True)
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

    print("RESET: Waiting for new Support / Resistance", flush=True)


# =====================================================
# METAAPI CONNECTION / ORDERS
# =====================================================

async def broker_order(action, volume, sl, tp):
    if api is None or not METAAPI_ACCOUNT_ID:
        return False, "MetaApi credentials are missing"

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

    if isinstance(result, dict):
        pid = result.get("positionId") or result.get("position_id")
    else:
        pid = None

    return True, {"result": result, "position_id": pid}


def place_broker_order(action, volume, sl, tp):
    try:
        return asyncio.run(
            broker_order(action, volume, sl, tp)
        )
    except Exception as e:
        print("MetaApi ORDER ERROR:", repr(e), flush=True)
        return False, str(e)


async def modify_broker_position(pid, new_sl, new_tp):
    if api is None or not METAAPI_ACCOUNT_ID or not pid:
        return False

    account = await api.metatrader_account_api.get_account(
        METAAPI_ACCOUNT_ID
    )

    await account.wait_connected()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    await connection.modify_position(pid, new_sl, new_tp)
    return True


def move_broker_sl_to_entry():
    if not position_id:
        print("Cannot move broker SL: position ID missing")
        return False

    try:
        return asyncio.run(
            modify_broker_position(position_id, entry, tp2)
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

        last_1h_time = candle1.get("openTime") if candle1 else None
        last_15m_time = candle15.get("openTime") if candle15 else None

        state = "WAITING_1H"

    send_telegram(
        "✅ SUPPORT / RESISTANCE UPDATED\n\n"
        f"🟦 Support: {support:.2f}\n"
        f"🟥 Resistance: {resistance:.2f}\n\n"
        "🤖 Bot is watching XAUUSD."
    )

    print("S/R UPDATED:", support, resistance, flush=True)


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

        data = response.json()

        if not data.get("ok"):
            print("Telegram getUpdates ERROR:", data)
            return

        for update in data.get("result", []):
            last_update_id = update["update_id"]

            message = update.get("message")
            if not message:
                continue

            text = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))

            if chat_id != str(CHAT_ID):
                continue

            print("Telegram Command:", text, flush=True)

            if text.startswith("/support"):
                parts = text.split()

                if len(parts) != 3:
                    send_telegram("Use:\n/support 4275 4300")
                    continue

                try:
                    s = float(parts[1])
                    r = float(parts[2])

                    if s >= r:
                        send_telegram(
                            "❌ Support must be below Resistance."
                        )
                        continue

                    set_support_resistance(s, r)

                except ValueError:
                    send_telegram("❌ Invalid prices. Example:\n/support 4275 4300")

            elif text == "/cancel":
                reset_everything()
                send_telegram(
                    "❌ SUPPORT / RESISTANCE CANCELLED\n\n"
                    "⏳ Waiting for new Support / Resistance."
                )

            elif text == "/status":
                if support is None:
                    send_telegram("Waiting for Support / Resistance.")
                else:
                    send_telegram(
                        "📊 XAUUSD BOT STATUS\n\n"
                        f"🟦 Support: {support:.2f}\n"
                        f"🟥 Resistance: {resistance:.2f}\n"
                        f"State: {state}\n"
                        f"Auto trade: {AUTO_TRADE}"
                    )

    except Exception as e:
        print("Telegram CHECK ERROR:", repr(e), flush=True)


# =====================================================
# 1H BREAKOUT
# =====================================================

def check_1h():
    global state, last_1h_time, last_15m_time

    candle = get_latest_closed("1h")
    if not candle:
        return

    candle_time = candle.get("openTime")

    if candle_time == last_1h_time:
        return

    last_1h_time = candle_time
    close_price = float(candle["close"])

    print(
        "NEW CLOSED 1H:", close_price,
        "Support:", support, "Resistance:", resistance,
        flush=True
    )

    if close_price >= resistance + 5.0:
        state = "WAITING_BUY_15M"

        current = get_latest_closed("15m")
        if current:
            last_15m_time = current.get("openTime")

        send_telegram(
            "🟢 Confirmation for Today BUY\n\n"
            "Wait for Confirmation Candle BUY 15M."
        )

    elif close_price <= support - 5.0:
        state = "WAITING_SELL_15M"

        current = get_latest_closed("15m")
        if current:
            last_15m_time = current.get("openTime")

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
    start_trade_record()

    order_text = "Signal only: AUTO_TRADE is disabled."

    if AUTO_TRADE:
        ok, result = place_broker_order(
            action, VOLUME, stop_loss, tp2
        )

        if ok:
            position_id = result.get("position_id")
            order_text = "Broker order request accepted."
        else:
            order_text = "❌ ORDER FAILED: " + str(result)
            send_telegram(order_text)

            # Do not manage a failed order as a real trade.
            reset_trade()
            state = "WAITING_1H"
            return

    send_telegram(
        f"{'🟢 BUY' if action == 'BUY' else '🔴 SELL'} SIGNAL\n\n"
        f"Pattern: {pattern}\n"
        f"Entry: {entry:.2f}\n"
        f"Stop Loss: {stop_loss:.2f}\n"
        f"TP1: {tp1:.2f}\n"
        f"TP2: {tp2:.2f}\n\n"
        f"{order_text}"
    )

    print(action, pattern, entry, stop_loss, tp1, tp2, flush=True)


# =====================================================
# 15M CONFIRMATION
# =====================================================

def check_confirmation(action):
    global last_15m_time

    candles = get_closed_bars("15m", 2)
    if len(candles) < 2:
        return

    current, previous = candles[0], candles[1]
    candle_time = current.get("openTime")

    if candle_time == last_15m_time:
        return

    last_15m_time = candle_time

    o = float(current["open"])
    h = float(current["high"])
    l = float(current["low"])
    c = float(current["close"])

    po = float(previous["open"])
    pc = float(previous["close"])

    if action == "BUY":
        hammer = is_hammer(o, h, l, c)
        engulf = is_bullish_engulfing(po, pc, o, c)

        if hammer:
            open_trade("BUY", current, "Hammer")
        elif engulf:
            open_trade("BUY", current, "Bullish Engulfing")

    else:
        star = is_shooting_star(o, h, l, c)
        engulf = is_bearish_engulfing(po, pc, o, c)

        if star:
            open_trade("SELL", current, "Shooting Star")
        elif engulf:
            open_trade("SELL", current, "Bearish Engulfing")


# =====================================================
# TRADE MANAGEMENT
# =====================================================

def manage_trade():
    global tp1_hit, stop_loss, last_manage_15m_time

    candle = get_latest_closed("15m")
    if not candle:
        return

    candle_time = candle.get("openTime")

    if candle_time == last_manage_15m_time:
        return

    last_manage_15m_time = candle_time

    high = float(candle["high"])
    low = float(candle["low"])

    is_buy = direction == "BUY"

    # Conservative assumption if SL and target overlap
    # within the same candle: handle the stop first.
    if not tp1_hit:
        stopped = low <= stop_loss if is_buy else high >= stop_loss

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

        reached_tp1 = high >= tp1 if is_buy else low <= tp1

        if reached_tp1:
            if AUTO_TRADE and not move_broker_sl_to_entry():
                send_telegram(
                    "⚠️ TP1 reached, but broker SL modification "
                    "could not be confirmed. Check the position manually."
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
        returned_to_entry = low <= entry if is_buy else high >= entry

        if returned_to_entry:
            finish_trade({"profit": 100, "stop_loss": 0})

            send_telegram(
                "⚠️ PRICE RETURNED TO ENTRY\n"
                "Check broker position status.\n"
                "Waiting for new Support / Resistance."
            )
            reset_everything()
            return

        reached_tp2 = high >= tp2 if is_buy else low <= tp2

        if reached_tp2:
            finish_trade({"profit": 150, "stop_loss": 0})

            send_telegram(
                "🎉 TP2 REACHED\n"
                "Check that the broker position is closed.\n"
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
            lines.append(f"Profit: {trade['profit']} points")
            lines.append(f"Stop Loss: {trade['stop_loss']} points")

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

    if now.weekday() != 4 or now.hour < 23:
        return

    week = now.strftime("%Y-%W")

    if week == last_report_week:
        return

    send_telegram(make_weekly_report())
    last_report_week = week

    weekly_results = {
        day: [] for day in
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    }


# =====================================================
# FLASK
# =====================================================

@app.route("/", methods=["GET"])
def index():
    return "XAUUSD Bot is running", 200


@app.route("/health", methods=["GET"])
def health():
    return {
        "status": "running",
        "state": state,
        "auto_trade": AUTO_TRADE
    }, 200


# =====================================================
# MAIN LOOP
# =====================================================

def run_bot_loop():
    print("XAUUSD BOT STARTED", flush=True)
    print("Timezone: Asia/Baghdad", flush=True)
    print("AUTO_TRADE:", AUTO_TRADE, flush=True)

    if not BOT_TOKEN or not CHAT_ID:
        print("WARNING: Telegram credentials missing", flush=True)

    if AUTO_TRADE and (not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID):
        print("WARNING: MetaApi credentials missing", flush=True)

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

            elif state in ("MANAGING_BUY", "MANAGING_SELL"):
                manage_trade()

            time.sleep(15)

        except Exception as e:
            print("MAIN LOOP ERROR:", repr(e), flush=True)
            time.sleep(15)


if __name__ == "__main__":
    threading.Thread(
        target=run_bot_loop,
        daemon=True
    ).start()

    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
