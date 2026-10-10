import os
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

# =========================================================
# SETTINGS
# =========================================================

URL = "https://biquote.io/api/XAUUSD/ohlc"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

IRAQ_TZ = ZoneInfo("Asia/Baghdad")
POLL_SECONDS = 3

if not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError(
        "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Railway Variables"
    )

# =========================================================
# BOT STATE
# =========================================================

support = None
resistance = None
state = "WAITING_SUPPORT_RESISTANCE"

last_1h_time = None
last_15m_time = None
last_manage_15m_time = None

# Prevent checking market data more than once per time slot
last_1h_slot = None
last_15m_slot = None

last_update_id = 0

direction = None
entry = None
stop_loss = None
tp1 = None
tp2 = None
tp1_hit = False

weekly_results = {
    "Monday": [],
    "Tuesday": [],
    "Wednesday": [],
    "Thursday": [],
    "Friday": []
}

current_trade = None
last_report_week = None

# =========================================================
# TIME
# =========================================================

def iraq_now():
    return datetime.now(IRAQ_TZ)


def today_name():
    return iraq_now().strftime("%A")


def is_weekend():
    return iraq_now().weekday() >= 5


def current_hour_slot(now):
    return now.strftime("%Y-%m-%d %H")


def current_15m_slot(now):
    return now.strftime("%Y-%m-%d %H:") + str(
        (now.minute // 15) * 15
    ).zfill(2)


def is_hour_close_time(now):
    return now.minute == 0 and now.second >= 5


def is_15m_close_time(now):
    return now.minute in (0, 15, 30, 45) and now.second >= 5


# =========================================================
# CANDLE PATTERNS
# =========================================================

def is_hammer(o, h, l, c):
    body = abs(c - o)
    if body == 0:
        return False

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    return lower_wick >= body * 2 and upper_wick <= body


def is_shooting_star(o, h, l, c):
    body = abs(c - o)
    if body == 0:
        return False

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    return upper_wick >= body * 2 and lower_wick <= body


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


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):
    try:
        telegram_url = (
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        )

        response = requests.post(
            telegram_url,
            data={
                "chat_id": CHAT_ID,
                "text": message
            },
            timeout=10
        )

        print("Telegram:", response.status_code)

        if response.status_code != 200:
            print("Telegram response:", response.text[:300])

    except Exception as e:
        print("Telegram ERROR:", e)


def check_telegram():
    global last_update_id

    try:
        telegram_url = (
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        )

        response = requests.get(
            telegram_url,
            params={
                "offset": last_update_id + 1,
                "timeout": 1
            },
            timeout=5
        )

        data = response.json()

        if not data.get("ok"):
            return

        for update in data.get("result", []):
            last_update_id = update["update_id"]

            message = update.get("message")
            if not message:
                continue

            text = message.get("text", "").strip()
            chat_id = str(message["chat"]["id"])

            if chat_id != str(CHAT_ID):
                continue

            # SUPPORT / RESISTANCE
            if text.startswith("/support"):
                parts = text.split()

                if len(parts) != 3:
                    send_telegram(
                        "❌ Wrong format\n\n"
                        "Use:\n/support 4275 4300"
                    )
                    continue

                try:
                    new_support = float(parts[1])
                    new_resistance = float(parts[2])

                    if new_support >= new_resistance:
                        send_telegram(
                            "❌ Support must be below Resistance."
                        )
                        continue

                    set_support_resistance(
                        new_support,
                        new_resistance
                    )

                except ValueError:
                    send_telegram(
                        "❌ Wrong numbers.\n\n"
                        "Use:\n/support 4275 4300"
                    )

            # CANCEL
            elif text == "/cancel":
                reset_everything()

                send_telegram(
                    "❌ SUPPORT / RESISTANCE CANCELLED\n\n"
                    "⏳ Waiting for new Support / Resistance.\n\n"
                    "Use:\n/support 4275 4300"
                )

            # STATUS
            elif text == "/status":
                if support is None:
                    send_telegram(
                        "⚠️ Support / Resistance not set.\n\n"
                        "/support 4275 4300"
                    )
                else:
                    send_telegram(
                        "📊 XAUUSD BOT STATUS\n\n"
                        f"🟦 Support: {support:.2f}\n"
                        f"🟥 Resistance: {resistance:.2f}\n"
                        f"🤖 State: {state}\n"
                        f"📈 Direction: {direction or 'Not confirmed'}"
                    )

    except Exception as e:
        print("Telegram CHECK ERROR:", e)


# =========================================================
# MARKET DATA
# =========================================================

def get_bars(interval, limit=10):
    try:
        response = requests.get(
            URL,
            params={
                "interval": interval,
                "limit": limit
            },
            timeout=10
        )
        response.raise_for_status()

        data = response.json()

        if not isinstance(data, dict) or "bars" not in data:
            print("Unexpected market API response")
            return []

        bars = data["bars"]

        if not isinstance(bars, list):
            print("Market API bars is not a list")
            return []

        # API must provide openTime and isOpen as expected
        bars = sorted(
            bars,
            key=lambda x: str(x.get("openTime", "")),
            reverse=True
        )

        return bars

    except Exception as e:
        print("Market ERROR:", e)
        return []


def get_latest_closed(interval):
    bars = get_bars(interval)

    for candle in bars:
        if candle.get("isOpen") is False:
            return candle

    return None


def get_two_closed_15m():
    bars = get_bars("15m")
    closed = [
        candle for candle in bars
        if candle.get("isOpen") is False
    ]

    if len(closed) < 2:
        return None, None

    return closed[0], closed[1]


# =========================================================
# RESET TRADE
# =========================================================

def reset_trade():
    global direction, entry, stop_loss, tp1, tp2
    global tp1_hit, current_trade, last_manage_15m_time

    direction = None
    entry = None
    stop_loss = None
    tp1 = None
    tp2 = None
    tp1_hit = False
    current_trade = None
    last_manage_15m_time = None


def reset_everything():
    global support, resistance, state
    global last_1h_time, last_15m_time
    global last_1h_slot, last_15m_slot

    support = None
    resistance = None
    state = "WAITING_SUPPORT_RESISTANCE"

    last_1h_time = None
    last_15m_time = None
    last_1h_slot = None
    last_15m_slot = None

    reset_trade()

    print("================================")
    print("RESET")
    print("Waiting for new Support / Resistance")
    print("================================")


# =========================================================
# SET SUPPORT / RESISTANCE
# =========================================================

def set_support_resistance(new_support, new_resistance):
    global support, resistance, state
    global last_1h_time, last_15m_time
    global last_1h_slot, last_15m_slot

    support = new_support
    resistance = new_resistance
    state = "WAITING_1H"

    reset_trade()

    # Prevent evaluating an old candle as a new candle
    current_1h = get_latest_closed("1h")
    last_1h_time = (
        current_1h.get("openTime") if current_1h else None
    )

    current_15m = get_latest_closed("15m")
    last_15m_time = (
        current_15m.get("openTime") if current_15m else None
    )

    # Only check the next scheduled close
    now = iraq_now()
    last_1h_slot = current_hour_slot(now)
    last_15m_slot = current_15m_slot(now)

    print("================================")
    print("NEW SUPPORT / RESISTANCE")
    print("SUPPORT:", support)
    print("RESISTANCE:", resistance)
    print("STATE:", state)
    print("================================")

    send_telegram(
        "✅ SUPPORT / RESISTANCE UPDATED\n\n"
        f"🟦 Support: {support:.2f}\n"
        f"🟥 Resistance: {resistance:.2f}\n\n"
        "🤖 Bot is watching XAUUSD."
    )


# =========================================================
# START / FINISH TRADE RECORD
# =========================================================

def start_trade_record():
    global current_trade

    current_trade = {
        "day": today_name(),
        "profit": 0,
        "stop_loss": 0,
        "status": "OPEN"
    }


def record_result(profit, sl_pips):
    global current_trade

    if current_trade is None:
        start_trade_record()

    day = current_trade["day"]

    if day in weekly_results:
        weekly_results[day].append({
            "profit": profit,
            "stop_loss": sl_pips
        })

    print("DAILY RESULT")
    print("Profit:", profit)
    print("Stop Loss:", sl_pips)

    current_trade = None


def finish_trade_tp1():
    record_result(100, 0)


def finish_trade_tp2():
    record_result(150, 0)


def finish_trade_sl():
    difference = abs(entry - stop_loss)
    sl_pips = round(difference * 10)
    record_result(0, sl_pips)


def finish_trade_breakeven():
    if current_trade is not None:
        record_result(100, 0)


# =========================================================
# 1 HOUR BREAKOUT
# =========================================================

def check_1h():
    global state, last_1h_time, last_15m_time

    if is_weekend():
        return

    candle = get_latest_closed("1h")

    if candle is None:
        return

    candle_time = candle.get("openTime")

    if candle_time == last_1h_time:
        print("No new closed 1H candle")
        return

    last_1h_time = candle_time
    close_price = float(candle["close"])

    print("================================")
    print("NEW CLOSED 1H")
    print("Close:", close_price)
    print("Support:", support)
    print("Resistance:", resistance)
    print("================================")

    # BUY: 5.00 or more above resistance
    if close_price >= resistance + 5.0:
        state = "WAITING_BUY_15M"

        current = get_latest_closed("15m")
        if current:
            last_15m_time = current.get("openTime")

        send_telegram(
            "confirmation for Today Buy\n\n"
            "wait for Confirmation Candle Buy 15M"
        )

        print("BUY DIRECTION")
        return

    # SELL: 5.00 or more below support
    if close_price <= support - 5.0:
        state = "WAITING_SELL_15M"

        current = get_latest_closed("15m")
        if current:
            last_15m_time = current.get("openTime")

        send_telegram(
            "confirmation for Today Sell\n\n"
            "wait for Confirmation Candle Sell 15M"
        )

        print("SELL DIRECTION")
        return

    print("NO 1H BREAKOUT")


# =========================================================
# BUY 15M CONFIRMATION
# =========================================================

def check_buy_confirmation():
    global state, last_15m_time
    global direction, entry, stop_loss, tp1, tp2, tp1_hit

    if is_weekend():
        return

    current, previous = get_two_closed_15m()

    if current is None or previous is None:
        return

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

    hammer = is_hammer(o, h, l, c)
    engulfing = is_bullish_engulfing(po, pc, o, c)

    if not (hammer or engulfing):
        print("15M: No BUY confirmation")
        return

    direction = "BUY"
    entry = c
    stop_loss = l - 3.0
    tp1 = entry + 10.0
    tp2 = entry + 15.0
    tp1_hit = False
    state = "MANAGING_BUY"

    start_trade_record()

    pattern = "Hammer" if hammer else "Bullish Engulfing"

    print("BUY SIGNAL", pattern, entry, stop_loss, tp1, tp2)

    send_telegram(
        "🟢 XAUUSD BUY SIGNAL\n\n"
        f"Pattern: {pattern}\n\n"
        f"Entry Buy point: {entry:.2f}\n\n"
        f"Stop: {stop_loss:.2f}\n\n"
        f"Profit_1: {tp1:.2f}\n\n"
        f"Profit_2: {tp2:.2f}"
    )


# =========================================================
# SELL 15M CONFIRMATION
# =========================================================

def check_sell_confirmation():
    global state, last_15m_time
    global direction, entry, stop_loss, tp1, tp2, tp1_hit

    if is_weekend():
        return

    current, previous = get_two_closed_15m()

    if current is None or previous is None:
        return

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

    shooting = is_shooting_star(o, h, l, c)
    engulfing = is_bearish_engulfing(po, pc, o, c)

    if not (shooting or engulfing):
        print("15M: No SELL confirmation")
        return

    direction = "SELL"
    entry = c
    stop_loss = h + 3.0
    tp1 = entry - 10.0
    tp2 = entry - 15.0
    tp1_hit = False
    state = "MANAGING_SELL"

    start_trade_record()

    pattern = "Shooting Star" if shooting else "Bearish Engulfing"

    print("SELL SIGNAL", pattern, entry, stop_loss, tp1, tp2)

    send_telegram(
        "🔴 XAUUSD SELL SIGNAL\n\n"
        f"Pattern: {pattern}\n\n"
        f"Entry Sell point: {entry:.2f}\n\n"
        f"Stop: {stop_loss:.2f}\n\n"
        f"Profit_1: {tp1:.2f}\n\n"
        f"Profit_2: {tp2:.2f}"
    )


# =========================================================
# MANAGE BUY
# =========================================================

def manage_buy():
    global tp1_hit, stop_loss, last_manage_15m_time

    candle = get_latest_closed("15m")
    if candle is None:
        return

    candle_time = candle.get("openTime")

    if candle_time == last_manage_15m_time:
        return

    last_manage_15m_time = candle_time

    high = float(candle["high"])
    low = float(candle["low"])

    if not tp1_hit:
        # Conservative assumption if SL and TP are both inside candle:
        # handle SL first because intrabar order is unknown.
        if low <= stop_loss:
            finish_trade_sl()

            send_telegram(
                "SORRY STOP LOSS\n\n"
                "«ئەرکی من پێشبینیکردنی هەموو تریدێک نییە؛ "
                "ئەرکی من جێبەجێکردنی پلانی خۆمە.»\n\n"
                "⛔ No more trade.\n"
                "⏳ Waiting for new Support / Resistance."
            )

            reset_everything()
            return

        if high >= tp1:
            tp1_hit = True
            stop_loss = entry

            send_telegram(
                "CONGRATULATIONS 🎉\n\n"
                "پیروزبێت تارگێتی یەکەم.\n\n"
                "Stop Loss بێنە شوێنی داخل بوون.\n\n"
                f"Entry: {entry:.2f}\n"
                f"New Stop: {entry:.2f}\n\n"
                "⏳ Waiting for TP2."
            )
            return

    else:
        if low <= entry:
            finish_trade_breakeven()

            send_telegram(
                "⚠️ NOW NO TRADE\n\n"
                "بازار گەڕایەوە بۆ شوێنی داخل بوون.\n\n"
                "⏳ چاوەڕێی Support / Resistance ـی نوێ بکە."
            )

            reset_everything()
            return

        if high >= tp2:
            finish_trade_tp2()

            send_telegram(
                "CONGRATULATIONS 🎉\n\n"
                "پیروزبێت تارگێتی دووەم.\n\n"
                "⏳ Waiting for new Support / Resistance."
            )

            reset_everything()
            return


# =========================================================
# MANAGE SELL
# =========================================================

def manage_sell():
    global tp1_hit, stop_loss, last_manage_15m_time

    candle = get_latest_closed("15m")
    if candle is None:
        return

    candle_time = candle.get("openTime")

    if candle_time == last_manage_15m_time:
        return

    last_manage_15m_time = candle_time

    high = float(candle["high"])
    low = float(candle["low"])

    if not tp1_hit:
        # Conservative assumption: SL first if both levels touched.
        if high >= stop_loss:
            finish_trade_sl()

            send_telegram(
                "SORRY STOP LOSS\n\n"
                "«ئەرکی من پێشبینیکردنی هەموو تریدێک نییە؛ "
                "ئەرکی من جێبەجێکردنی پلانی خۆمە.»\n\n"
                "⛔ No more trade.\n"
                "⏳ Waiting for new Support / Resistance."
            )

            reset_everything()
            return

        if low <= tp1:
            tp1_hit = True
            stop_loss = entry

            send_telegram(
                "CONGRATULATIONS 🎉\n\n"
                "پیروزبێت تارگێتی یەکەم.\n\n"
                "Stop Loss بێنە شوێنی داخل بوون.\n\n"
                f"Entry: {entry:.2f}\n"
                f"New Stop: {entry:.2f}\n\n"
                "⏳ Waiting for TP2."
            )
            return

    else:
        if high >= entry:
            finish_trade_breakeven()

            send_telegram(
                "⚠️ NOW NO TRADE\n\n"
                "بازار گەڕایەوە بۆ شوێنی داخل بوون.\n\n"
                "⏳ چاوەڕێی Support / Resistance ـی نوێ بکە."
            )

            reset_everything()
            return

        if low <= tp2:
            finish_trade_tp2()

            send_telegram(
                "CONGRATULATIONS 🎉\n\n"
                "پیروزبێت تارگێتی دووەم.\n\n"
                "⏳ Waiting for new Support / Resistance."
            )

            reset_everything()
            return


# =========================================================
# WEEKLY REPORT
# =========================================================

def make_weekly_report():
    total_profit = 0
    total_stop = 0
    lines = []

    day_names = [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
    ]

    for day in day_names:
        trades = weekly_results[day]

        lines.append("")
        lines.append(day)

        if not trades:
            lines.append("I didn't have a trade")
            continue

        for index, trade in enumerate(trades, start=1):
            lines.append(f"Signal {index}")
            lines.append(f"Profit: {trade['profit']} Pips")
            lines.append(f"Stop Loss: {trade['stop_loss']} Pips")
            lines.append("")

            total_profit += trade["profit"]
            total_stop += trade["stop_loss"]

    lines.extend([
        "",
        "━━━━━━━━━━━━━━",
        "",
        "Total Weekly",
        "",
        f"Take Profit: {total_profit} Pips",
        f"Stop Loss: {total_stop} Pips"
    ])

    return "📊 Weekly Result\n" + "\n".join(lines)


def check_weekly_report():
    global last_report_week, weekly_results

    now = iraq_now()

    if now.weekday() != 4 or now.hour < 23:
        return

    current_week = now.strftime("%Y-%W")

    if last_report_week == current_week:
        return

    report = make_weekly_report()

    print("WEEKLY REPORT")
    print(report)
    send_telegram(report)

    last_report_week = current_week

    weekly_results = {
        "Monday": [],
        "Tuesday": [],
        "Wednesday": [],
        "Thursday": [],
        "Friday": []
    }


# =========================================================
# MAIN
# =========================================================

print("================================")
print("XAUUSD TELEGRAM BOT STARTED")
print("Time zone: Iraq / Erbil UTC+3")
print("1H checks: hourly candle close")
print("15M checks: 00, 15, 30, 45")
print("Weekly report: Friday 23:00")
print("================================")

while True:
    try:
        # Telegram remains responsive
        check_telegram()
        check_weekly_report()

        if support is None or resistance is None:
            time.sleep(POLL_SECONDS)
            continue

        now = iraq_now()

        # Do not generate new signals during weekends
        if is_weekend():
            time.sleep(POLL_SECONDS)
            continue

        # 1H breakout: once per hourly closing slot
        if state == "WAITING_1H":
            slot = current_hour_slot(now)

            if is_hour_close_time(now) and slot != last_1h_slot:
                last_1h_slot = slot
                check_1h()

        # 15M confirmation and trade management:
        # once per 15-minute closing slot
        elif state in (
            "WAITING_BUY_15M",
            "WAITING_SELL_15M",
            "MANAGING_BUY",
            "MANAGING_SELL"
        ):
            slot = current_15m_slot(now)

            if is_15m_close_time(now) and slot != last_15m_slot:
                last_15m_slot = slot

                if state == "WAITING_BUY_15M":
                    check_buy_confirmation()

                elif state == "WAITING_SELL_15M":
                    check_sell_confirmation()

                elif state == "MANAGING_BUY":
                    manage_buy()

                elif state == "MANAGING_SELL":
                    manage_sell()

        time.sleep(POLL_SECONDS)

    except Exception as e:
        print("MAIN ERROR:", e)
        time.sleep(POLL_SECONDS)
