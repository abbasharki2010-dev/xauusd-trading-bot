import requests
import time
from datetime import datetime
from zoneinfo import ZoneInfo


# =========================================================
# SETTINGS
# =========================================================

URL = "https://biquote.io/api/XAUUSD/ohlc"

BOT_TOKEN = "8619686161:AAEyfJQPMkowak5GCluszGZ9N7lGUQ0QVms"
CHAT_ID = "942043461"

# هەولێر / عێراق = UTC+3
IRAQ_TZ = ZoneInfo("Asia/Baghdad")


# =========================================================
# BOT STATE
# =========================================================

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


# =========================================================
# WEEKLY RESULT
# =========================================================

weekly_results = {
    "Monday": [],
    "Tuesday": [],
    "Wednesday": [],
    "Thursday": [],
    "Friday": []
}

current_trade = None

weekly_report_sent = False
last_report_week = None


# =========================================================
# TIME
# =========================================================

def iraq_now():
    return datetime.now(IRAQ_TZ)


def today_name():
    return iraq_now().strftime("%A")


# =========================================================
# CANDLE PATTERNS
# =========================================================

def is_hammer(o, h, l, c):
    body = abs(c - o)

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    return (
        body > 0
        and lower_wick >= body * 2
        and upper_wick <= body
    )


def is_shooting_star(o, h, l, c):
    body = abs(c - o)

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    return (
        body > 0
        and upper_wick >= body * 2
        and lower_wick <= body
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

    except Exception as e:

        print("Telegram ERROR:", e)


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

        data = response.json()

        if "bars" not in data:
            return []

        return data["bars"]

    except Exception as e:

        print("Market ERROR:", e)
        return []


def get_latest_closed(interval):

    bars = get_bars(interval)

    for candle in bars:

        if candle.get("isOpen") == False:
            return candle

    return None


def get_two_closed_15m():

    bars = get_bars("15m")

    closed = []

    for candle in bars:

        if candle.get("isOpen") == False:

            closed.append(candle)

        if len(closed) == 2:
            break

    if len(closed) < 2:
        return None, None

    return closed[0], closed[1]


# =========================================================
# RESET TRADE
# =========================================================

def reset_trade():

    global direction
    global entry
    global stop_loss
    global tp1
    global tp2
    global tp1_hit
    global current_trade
    global last_manage_15m_time

    direction = None
    entry = None
    stop_loss = None
    tp1 = None
    tp2 = None
    tp1_hit = False
    current_trade = None
    last_manage_15m_time = None


# =========================================================
# RESET EVERYTHING
# =========================================================

def reset_everything():

    global support
    global resistance
    global state
    global last_1h_time
    global last_15m_time

    support = None
    resistance = None

    state = "WAITING_SUPPORT_RESISTANCE"

    last_1h_time = None
    last_15m_time = None

    reset_trade()

    print("================================")
    print("RESET")
    print("Waiting for new Support / Resistance")
    print("================================")


# =========================================================
# START NEW TRADE RECORD
# =========================================================

def start_trade_record():

    global current_trade

    current_trade = {
        "day": today_name(),
        "profit": 0,
        "stop_loss": 0,
        "status": "OPEN"
    }

    print("NEW TRADE RECORD")
    print("Day:", current_trade["day"])


# =========================================================
# FINISH TRADE - TP1
# =========================================================

def finish_trade_tp1():

    global current_trade

    if current_trade is None:
        start_trade_record()

    current_trade["profit"] = 100
    current_trade["stop_loss"] = 0
    current_trade["status"] = "TP1"

    day = current_trade["day"]

    weekly_results[day].append({
        "profit": 100,
        "stop_loss": 0
    })

    print("DAILY RESULT")
    print("Profit:", 100)
    print("Stop Loss:", 0)

    current_trade = None


# =========================================================
# FINISH TRADE - TP2
# =========================================================

def finish_trade_tp2():

    global current_trade

    if current_trade is None:
        start_trade_record()

    current_trade["profit"] = 150
    current_trade["stop_loss"] = 0
    current_trade["status"] = "TP2"

    day = current_trade["day"]

    weekly_results[day].append({
        "profit": 150,
        "stop_loss": 0
    })

    print("DAILY RESULT")
    print("Profit:", 150)
    print("Stop Loss:", 0)

    current_trade = None


# =========================================================
# FINISH TRADE - STOP LOSS
# =========================================================

def finish_trade_sl():

    global current_trade

    if current_trade is None:
        start_trade_record()

    difference = abs(entry - stop_loss)

    sl_pips = round(difference * 10)

    current_trade["profit"] = 0
    current_trade["stop_loss"] = sl_pips
    current_trade["status"] = "SL"

    day = current_trade["day"]

    weekly_results[day].append({
        "profit": 0,
        "stop_loss": sl_pips
    })

    print("DAILY RESULT")
    print("Profit:", 0)
    print("Stop Loss:", sl_pips)

    current_trade = None


# =========================================================
# FINISH TRADE - BREAK EVEN AFTER TP1
# =========================================================

def finish_trade_breakeven():

    global current_trade

    if current_trade is None:
        return

    # چون TP1 گرتووە، ئەنجامی تریدەکە +100 pip ـە
    current_trade["profit"] = 100
    current_trade["stop_loss"] = 0
    current_trade["status"] = "TP1_BE"

    day = current_trade["day"]

    weekly_results[day].append({
        "profit": 100,
        "stop_loss": 0
    })

    print("DAILY RESULT")
    print("Profit:", 100)
    print("Stop Loss:", 0)

    current_trade = None


# =========================================================
# SET SUPPORT / RESISTANCE
# =========================================================

def set_support_resistance(new_support, new_resistance):

    global support
    global resistance
    global state
    global last_1h_time
    global last_15m_time

    support = new_support
    resistance = new_resistance

    state = "WAITING_1H"

    reset_trade()

    current_1h = get_latest_closed("1h")

    if current_1h:
        last_1h_time = current_1h["openTime"]
    else:
        last_1h_time = None

    current_15m = get_latest_closed("15m")

    if current_15m:
        last_15m_time = current_15m["openTime"]
    else:
        last_15m_time = None

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
# TELEGRAM COMMANDS
# =========================================================

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


            # ---------------------------------------------
            # SUPPORT / RESISTANCE
            # ---------------------------------------------

            if text.startswith("/support"):

                parts = text.split()

                if len(parts) != 3:

                    send_telegram(
                        "❌ Wrong format\n\n"
                        "Use:\n"
                        "/support 4275 4300"
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

                except Exception as e:

                    print("S/R ERROR:", e)

                    send_telegram(
                        "❌ Wrong format\n\n"
                        "Use:\n"
                        "/support 4275 4300"
                    )


            # ---------------------------------------------
            # CANCEL
            # ---------------------------------------------

            elif text == "/cancel":

                reset_everything()

                send_telegram(
                    "❌ SUPPORT / RESISTANCE CANCELLED\n\n"
                    "⏳ Waiting for new Support / Resistance.\n\n"
                    "Use:\n"
                    "/support 4275 4300"
                )


            # ---------------------------------------------
            # STATUS
            # ---------------------------------------------

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
                        f"🤖 State: {state}"
                    )


    except Exception as e:

        print("Telegram CHECK ERROR:", e)


# =========================================================
# 1 HOUR BREAKOUT
# =========================================================

def check_1h():

    global state
    global last_1h_time
    global last_15m_time

    candle = get_latest_closed("1h")

    if candle is None:
        return

    candle_time = candle["openTime"]

    if candle_time == last_1h_time:
        return

    last_1h_time = candle_time

    close_price = float(candle["close"])

    print("================================")
    print("NEW CLOSED 1H")
    print("Close:", close_price)
    print("Support:", support)
    print("Resistance:", resistance)
    print("================================")


    # BUY
    if close_price >= resistance + 5.0:

        state = "WAITING_BUY_15M"

        current = get_latest_closed("15m")

        if current:
            last_15m_time = current["openTime"]

        send_telegram(
            "confirmation for Today Buy\n\n"
            "wait for Confirmation Candle Buy 15M"
        )

        print("BUY DIRECTION")

        return


    # SELL
    if close_price <= support - 5.0:

        state = "WAITING_SELL_15M"

        current = get_latest_closed("15m")

        if current:
            last_15m_time = current["openTime"]

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

    global state
    global last_15m_time
    global direction
    global entry
    global stop_loss
    global tp1
    global tp2
    global tp1_hit

    current, previous = get_two_closed_15m()

    if current is None or previous is None:
        return

    candle_time = current["openTime"]

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

    engulfing = is_bullish_engulfing(
        po,
        pc,
        o,
        c
    )


    if hammer or engulfing:

        direction = "BUY"

        entry = c

        stop_loss = l - 3.0

        tp1 = entry + 10.0

        tp2 = entry + 15.0

        tp1_hit = False

        state = "MANAGING_BUY"

        start_trade_record()

        pattern = (
            "Hammer"
            if hammer
            else "Bullish Engulfing"
        )

        print("================================")
        print("BUY SIGNAL")
        print("Pattern:", pattern)
        print("Entry:", entry)
        print("Stop:", stop_loss)
        print("TP1:", tp1)
        print("TP2:", tp2)
        print("================================")

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

    global state
    global last_15m_time
    global direction
    global entry
    global stop_loss
    global tp1
    global tp2
    global tp1_hit

    current, previous = get_two_closed_15m()

    if current is None or previous is None:
        return

    candle_time = current["openTime"]

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

    engulfing = is_bearish_engulfing(
        po,
        pc,
        o,
        c
    )


    if shooting or engulfing:

        direction = "SELL"

        entry = c

        stop_loss = h + 3.0

        tp1 = entry - 10.0

        tp2 = entry - 15.0

        tp1_hit = False

        state = "MANAGING_SELL"

        start_trade_record()

        pattern = (
            "Shooting Star"
            if shooting
            else "Bearish Engulfing"
        )

        print("================================")
        print("SELL SIGNAL")
        print("Pattern:", pattern)
        print("Entry:", entry)
        print("Stop:", stop_loss)
        print("TP1:", tp1)
        print("TP2:", tp2)
        print("================================")

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

    global tp1_hit
    global stop_loss
    global last_manage_15m_time

    candle = get_latest_closed("15m")

    if candle is None:
        return

    candle_time = candle["openTime"]

    # هەر کاندل تەنها یەک جار پشکنین بکرێت
    if candle_time == last_manage_15m_time:
        return

    last_manage_15m_time = candle_time

    high = float(candle["high"])
    low = float(candle["low"])


    # ---------------------------------------------
    # BEFORE TP1
    # ---------------------------------------------

    if not tp1_hit:

        # STOP LOSS
        if low <= stop_loss:

            print("BUY STOP LOSS HIT")

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


        # TP1
        if high >= tp1:

            tp1_hit = True

            stop_loss = entry

            print("BUY TP1 HIT")
            print("NEW STOP:", stop_loss)

            send_telegram(
                "CONGRATULATIONS 🎉\n\n"
                "پیروزبێت تارگێتی یەکەم.\n\n"
                "Stop Loss بێنە شوێنی داخل بوون.\n\n"
                f"Entry: {entry:.2f}\n"
                f"New Stop: {entry:.2f}\n\n"
                "⏳ Waiting for TP2."
            )

            return


    # ---------------------------------------------
    # AFTER TP1
    # ---------------------------------------------

    else:

        # RETURN TO ENTRY
        if low <= entry:

            print("BUY RETURNED TO ENTRY")

            finish_trade_breakeven()

            send_telegram(
                "⚠️ NOW NO TRADE\n\n"
                "بازار گەڕایەوە بۆ شوێنی داخل بوون.\n\n"
                "هیچ ئیعازێکی نوێ مەدە.\n"
                "⏳ چاوەڕێی Support / Resistance ـی نوێ بکە."
            )

            reset_everything()

            return


        # TP2
        if high >= tp2:

            print("BUY TP2 HIT")

            finish_trade_tp2()

            send_telegram(
                "CONGRATULATIONS 🎉\n\n"
                "پیروزبێت تارگێتی دووەم.\n\n"
                "هەموو مامەڵەکان قەپات بکە.\n\n"
                "⏳ Waiting for new Support / Resistance."
            )

            reset_everything()

            return


# =========================================================
# MANAGE SELL
# =========================================================

def manage_sell():

    global tp1_hit
    global stop_loss
    global last_manage_15m_time

    candle = get_latest_closed("15m")

    if candle is None:
        return

    candle_time = candle["openTime"]

    # هەر کاندل تەنها یەک جار پشکنین بکرێت
    if candle_time == last_manage_15m_time:
        return

    last_manage_15m_time = candle_time

    high = float(candle["high"])
    low = float(candle["low"])


    # ---------------------------------------------
    # BEFORE TP1
    # ---------------------------------------------

    if not tp1_hit:

        # STOP LOSS
        if high >= stop_loss:

            print("SELL STOP LOSS HIT")

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


        # TP1
        if low <= tp1:

            tp1_hit = True

            stop_loss = entry

            print("SELL TP1 HIT")
            print("NEW STOP:", stop_loss)

            send_telegram(
                "CONGRATULATIONS 🎉\n\n"
                "پیروزبێت تارگێتی یەکەم.\n\n"
                "Stop Loss بێنە شوێنی داخل بوون.\n\n"
                f"Entry: {entry:.2f}\n"
                f"New Stop: {entry:.2f}\n\n"
                "⏳ Waiting for TP2."
            )

            return


    # ---------------------------------------------
    # AFTER TP1
    # ---------------------------------------------

    else:

        # RETURN TO ENTRY
        if high >= entry:

            print("SELL RETURNED TO ENTRY")

            finish_trade_breakeven()

            send_telegram(
                "⚠️ NOW NO TRADE\n\n"
                "بازار گەڕایەوە بۆ شوێنی داخل بوون.\n\n"
                "هیچ ئیعازێکی نوێ مەدە.\n"
                "⏳ چاوەڕێی Support / Resistance ـی نوێ بکە."
            )

            reset_everything()

            return


        # TP2
        if low <= tp2:

            print("SELL TP2 HIT")

            finish_trade_tp2()

            send_telegram(
                "CONGRATULATIONS 🎉\n\n"
                "پیروزبێت تارگێتی دووەم.\n\n"
                "هەموو مامەڵەکان قەپات بکە.\n\n"
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
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday"
    ]

    for day in day_names:

        trades = weekly_results[day]

        lines.append("")
        lines.append(day)

        if not trades:

            lines.append("I didn't have a trade")

            continue


        for index, trade in enumerate(trades, start=1):

            lines.append(
                f"Signal {index}"
            )

            lines.append(
                f"Profit: {trade['profit']} Pips"
            )

            lines.append(
                f"Stop Loss: {trade['stop_loss']} Pips"
            )

            lines.append("")

            total_profit += trade["profit"]

            total_stop += trade["stop_loss"]


    lines.append("")
    lines.append("━━━━━━━━━━━━━━")
    lines.append("")
    lines.append("Total Weekly")
    lines.append("")
    lines.append(
        f"Take Profit: {total_profit} Pips"
    )
    lines.append(
        f"Stop Loss: {total_stop} Pips"
    )

    return "📊 Weekly Result\n" + "\n".join(lines)


# =========================================================
# SEND WEEKLY REPORT
# =========================================================

def check_weekly_report():

    global weekly_report_sent
    global last_report_week
    global weekly_results

    now = iraq_now()

    # هەینی تەنها
    if now.weekday() != 4:
        return

    # 11:00 شەو
    if now.hour < 23:
        return

    current_week = now.strftime("%Y-%W")

    if last_report_week == current_week:
        return

    report = make_weekly_report()

    print("================================")
    print("WEEKLY REPORT")
    print(report)
    print("================================")

    send_telegram(report)

    last_report_week = current_week

    # هەفتەی نوێ
    weekly_results = {
        "Monday": [],
        "Tuesday": [],
        "Wednesday": [],
        "Thursday": [],
        "Friday": []
    }

    weekly_report_sent = True

    print("WEEKLY RESULTS RESET")


# =========================================================
# MAIN
# =========================================================

print("================================")
print("XAUUSD TELEGRAM BOT STARTED")
print("================================")

print("Time zone: Iraq / Erbil UTC+3")
print("Weekly report: Friday 23:00")
print("================================")


while True:

    try:

        # Telegram commands
        check_telegram()


        # Weekly report
        check_weekly_report()


        # No S/R
        if support is None or resistance is None:

            print(
                "Waiting for Support / Resistance..."
            )

            time.sleep(15)

            continue


        # 1H
        if state == "WAITING_1H":

            check_1h()


        # BUY 15M
        elif state == "WAITING_BUY_15M":

            check_buy_confirmation()


        # SELL 15M
        elif state == "WAITING_SELL_15M":

            check_sell_confirmation()


        # MANAGE BUY
        elif state == "MANAGING_BUY":

            manage_buy()


        # MANAGE SELL
        elif state == "MANAGING_SELL":

            manage_sell()


        time.sleep(15)


    except Exception as e:

        print("MAIN ERROR:", e)

        time.sleep(15)
