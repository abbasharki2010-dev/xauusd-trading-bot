import os
import time
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# ==========================================
# XAUUSD TELEGRAM BOT
# ==========================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "942043461").strip()

API_URL = "https://biquote.io/api/XAUUSD/ohlc"
TELEGRAM_URL = "https://api.telegram.org/bot" + BOT_TOKEN

IRAQ_TZ = ZoneInfo("Asia/Baghdad")

# یاساکانی بۆت
BREAKOUT_POINTS = 5
SL_EXTRA = 3
TP1_POINTS = 10
TP2_POINTS = 15

CHECK_INTERVAL = 15

# ==========================================
# STATE
# ==========================================

support = None
resistance = None

state = "WAITING_SR"
direction = None

last_update_id = 0
last_1h_time = None
last_15m_time = None

entry = None
stop_loss = None
take_profit_1 = None
take_profit_2 = None

tp1_hit = False
trade_active = False

weekly_wins = 0
weekly_losses = 0
weekly_report_sent = None


# ==========================================
# TELEGRAM
# ==========================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: Telegram environment variables missing")
        return False

    try:
        response = requests.post(
            TELEGRAM_URL + "/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": message
            },
            timeout=15
        )

        print("TELEGRAM SEND HTTP:", response.status_code)
        print("TELEGRAM SEND RESPONSE:", response.text[:500])

        return response.ok and response.json().get("ok", False)

    except Exception as error:
        print("TELEGRAM SEND ERROR:", repr(error))
        return False


# ==========================================
# RESET
# ==========================================

def reset_everything():
    global support, resistance, state, direction
    global last_1h_time, last_15m_time
    global entry, stop_loss, take_profit_1, take_profit_2
    global tp1_hit, trade_active

    support = None
    resistance = None

    state = "WAITING_SR"
    direction = None

    last_1h_time = None
    last_15m_time = None

    entry = None
    stop_loss = None
    take_profit_1 = None
    take_profit_2 = None

    tp1_hit = False
    trade_active = False

    print("BOT RESET", flush=True)


# ==========================================
# SET SUPPORT / RESISTANCE
# ==========================================

def set_support_resistance(new_support, new_resistance):
    global support, resistance, state, direction
    global last_1h_time, last_15m_time
    global entry, stop_loss, take_profit_1, take_profit_2
    global tp1_hit, trade_active

    if new_support >= new_resistance:
        send_telegram(
            "❌ ERROR\n"
            "Support دەبێت لە Resistance نزمتر بێت."
        )
        return

    support = float(new_support)
    resistance = float(new_resistance)

    state = "WAITING_1H"
    direction = None

    last_1h_time = None
    last_15m_time = None

    entry = None
    stop_loss = None
    take_profit_1 = None
    take_profit_2 = None

    tp1_hit = False
    trade_active = False

    message = (
        "✅ XAUUSD S/R UPDATED\n\n"
        f"🟦 Support: {support:.2f}\n"
        f"🟥 Resistance: {resistance:.2f}\n\n"
        "🤖 STATE: WAITING_1H\n"
        "چاوەڕێی داخستنی کاندلی 1H."
    )

    print(message, flush=True)
    send_telegram(message)


# ==========================================
# TELEGRAM COMMANDS
# ==========================================

def check_telegram():
    global last_update_id

    try:
        response = requests.get(
            TELEGRAM_URL + "/getUpdates",
            params={
                "offset": last_update_id + 1,
                "timeout": 1
            },
            timeout=5
        )

        print("GETUPDATES HTTP:", response.status_code)

        data = response.json()

        if not data.get("ok"):
            print("GETUPDATES ERROR:", data)
            return

        for update in data.get("result", []):
            last_update_id = update["update_id"]

            message = update.get("message")

            if not message:
                continue

            text = message.get("text", "").strip()
            incoming_chat_id = str(message["chat"]["id"])

            print("INCOMING MESSAGE:", text)
            print("INCOMING CHAT ID:", incoming_chat_id)
            print("EXPECTED CHAT ID:", CHAT_ID)

            if incoming_chat_id != CHAT_ID:
                print("CHAT ID MISMATCH")
                continue

            if text == "/start":
                send_telegram(
                    "✅ XAUUSD Bot is online!\n\n"
                    "فەرمانەکان:\n"
                    "/support 4275 4300\n"
                    "/status\n"
                    "/cancel"
                )

            elif text.startswith("/support") or text.startswith("/sr"):
                parts = text.split()

                if len(parts) != 3:
                    send_telegram(
                        "❌ فۆرماتی هەڵەیە.\n"
                        "بەم شێوەیە بینووسە:\n"
                        "/support 4275 4300"
                    )
                    continue

                try:
                    new_support = float(parts[1])
                    new_resistance = float(parts[2])

                    set_support_resistance(
                        new_support,
                        new_resistance
                    )

                except ValueError:
                    send_telegram(
                        "❌ ژمارەکان هەڵەن.\n"
                        "نمونە: /support 4275 4300"
                    )

            elif text == "/cancel":
                reset_everything()

                send_telegram(
                    "❌ S/R CANCELLED\n\n"
                    "Support / Resistance هەڵوەشێنرایەوە.\n"
                    "چاوەڕێی S/R ـی نوێین."
                )

            elif text == "/status":
                if support is None:
                    send_telegram(
                        "S/R دانەنراوە.\n"
                        "نمونە: /support 4275 4300"
                    )
                else:
                    send_telegram(
                        "📊 XAUUSD BOT STATUS\n\n"
                        f"Support: {support:.2f}\n"
                        f"Resistance: {resistance:.2f}\n"
                        f"State: {state}\n"
                        f"Direction: {direction}\n"
                        f"Trade Active: {trade_active}"
                    )

    except Exception as error:
        print("TELEGRAM CHECK ERROR:", repr(error))


# ==========================================
# CANDLE DATA
# ==========================================

def parse_time(value):
    if value is None:
        return None

    try:
        if isinstance(value, (int, float)):
            stamp = float(value)

            if stamp > 100000000000:
                stamp = stamp / 1000

            return datetime.fromtimestamp(
                stamp,
                tz=timezone.utc
            )

        if isinstance(value, str):
            cleaned = value.strip()

            if cleaned.isdigit():
                return parse_time(int(cleaned))

            parsed = datetime.fromisoformat(
                cleaned.replace("Z", "+00:00")
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)

    except Exception:
        return None

    return None


def normalize_candle(raw):
    if not isinstance(raw, dict):
        return None

    def get_value(*names):
        for name in names:
            if name in raw and raw[name] is not None:
                return raw[name]
        return None

    try:
        open_price = float(
            get_value("open", "o", "Open")
        )
        high_price = float(
            get_value("high", "h", "High")
        )
        low_price = float(
            get_value("low", "l", "Low")
        )
        close_price = float(
            get_value("close", "c", "Close")
        )

        candle_time = parse_time(
            get_value(
                "time",
                "timestamp",
                "datetime",
                "date",
                "openTime",
                "open_time",
                "t"
            )
        )

        open_flag = get_value("isOpen", "is_open")

        if isinstance(open_flag, str):
            open_flag = open_flag.lower() == "true"

        elif open_flag is not None:
            open_flag = bool(open_flag)

        return {
            "time": candle_time,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "is_open": open_flag
        }

    except (TypeError, ValueError):
        return None


def extract_candles(payload, timeframe):
    candidates = []

    if isinstance(payload, list):
        candidates = payload

    elif isinstance(payload, dict):
        possible_keys = [
            timeframe,
            timeframe.upper(),
            "data",
            "candles",
            "ohlc",
            "result",
            "prices"
        ]

        for key in possible_keys:
            value = payload.get(key)

            if isinstance(value, list):
                candidates = value
                break

            if isinstance(value, dict):
                for nested_key in [
                    timeframe,
                    timeframe.upper(),
                    "data",
                    "candles",
                    "ohlc",
                    "result"
                ]:
                    nested_value = value.get(nested_key)

                    if isinstance(nested_value, list):
                        candidates = nested_value
                        break

                if candidates:
                    break

    result = []

    for raw in candidates:
        candle = normalize_candle(raw)

        if candle is not None:
            result.append(candle)

    return result


def get_closed_candles(timeframe):
    try:
        response = requests.get(
            API_URL,
            params={
                "timeframe": timeframe,
                "interval": timeframe
            },
            timeout=15
        )

        response.raise_for_status()
        payload = response.json()

        candles = extract_candles(payload, timeframe)

        if not candles:
            print(
                "NO CANDLES FOUND:",
                timeframe,
                str(payload)[:500]
            )
            return []

        now = datetime.now(timezone.utc)

        duration_minutes = 60 if timeframe == "1h" else 15

        closed = []

        for candle in candles:
            if candle["is_open"] is True:
                continue

            if candle["is_open"] is False:
                closed.append(candle)
                continue

            candle_time = candle["time"]

            if candle_time is not None:
                candle_end = (
                    candle_time.timestamp()
                    + duration_minutes * 60
                )

                if candle_end <= now.timestamp():
                    closed.append(candle)

        closed.sort(
            key=lambda candle: (
                candle["time"].timestamp()
                if candle["time"] is not None
                else 0
            )
        )

        return closed

    except Exception as error:
        print(
            "CANDLE API ERROR:",
            timeframe,
            repr(error)
        )
        return []


# ==========================================
# CANDLE PATTERNS
# ==========================================

def is_hammer(candle):
    o = candle["open"]
    h = candle["high"]
    l = candle["low"]
    c = candle["close"]

    body = abs(c - o)
    full_range = h - l

    if full_range <= 0:
        return False

    body = max(body, full_range * 0.02)

    lower_shadow = min(o, c) - l
    upper_shadow = h - max(o, c)

    return (
        lower_shadow >= body * 2
        and upper_shadow <= body
        and c > l + full_range * 0.55
    )


def is_shooting_star(candle):
    o = candle["open"]
    h = candle["high"]
    l = candle["low"]
    c = candle["close"]

    body = abs(c - o)
    full_range = h - l

    if full_range <= 0:
        return False

    body = max(body, full_range * 0.02)

    lower_shadow = min(o, c) - l
    upper_shadow = h - max(o, c)

    return (
        upper_shadow >= body * 2
        and lower_shadow <= body
        and c < l + full_range * 0.45
    )


def is_bullish_engulfing(previous, current):
    return (
        previous["close"] < previous["open"]
        and current["close"] > current["open"]
        and current["open"] <= previous["close"]
        and current["close"] >= previous["open"]
    )


def is_bearish_engulfing(previous, current):
    return (
        previous["close"] > previous["open"]
        and current["close"] < current["open"]
        and current["open"] >= previous["close"]
        and current["close"] <= previous["open"]
    )


# ==========================================
# 1H BREAKOUT CHECK
# ==========================================

def check_1h_breakout():
    global state, direction, last_1h_time

    if support is None or resistance is None:
        return

    candles = get_closed_candles("1h")

    if not candles:
        return

    candle = candles[-1]
    candle_time = candle["time"]

    if candle_time is None:
        print("1H candle has no timestamp")
        return

    if last_1h_time == candle_time:
        return

    last_1h_time = candle_time

    close_price = candle["close"]

    print("=" * 35)
    print("NEW CLOSED 1H CANDLE")
    print("1H CLOSE:", close_price)
    print("SUPPORT:", support)
    print("RESISTANCE:", resistance)
    print("STATE:", state)
    print("=" * 35)

    if state != "WAITING_1H":
        return

    if close_price >= resistance + BREAKOUT_POINTS:
        direction = "BUY"
        state = "WAITING_15M"

        send_telegram(
            "🟢 CONFIRMATION FOR TODAY: BUY\n\n"
            f"Resistance: {resistance:.2f}\n"
            f"1H Close: {close_price:.2f}\n\n"
            "چاوەڕێی کاندلی پشتڕاستکردنەوەی 15M بە.\n"
            "Hammer یان Bullish Engulfing."
        )

    elif close_price <= support - BREAKOUT_POINTS:
        direction = "SELL"
        state = "WAITING_15M"

        send_telegram(
            "🔴 CONFIRMATION FOR TODAY: SELL\n\n"
            f"Support: {support:.2f}\n"
            f"1H Close: {close_price:.2f}\n\n"
            "چاوەڕێی کاندلی پشتڕاستکردنەوەی 15M بە.\n"
            "Shooting Star یان Bearish Engulfing."
        )

    else:
        print("NO 1H BREAKOUT")


# ==========================================
# CREATE TRADE
# ==========================================

def create_trade(candle):
    global state, entry, stop_loss
    global take_profit_1, take_profit_2
    global tp1_hit, trade_active, last_15m_time

    entry = candle["close"]

    if direction == "BUY":
        stop_loss = candle["low"] - SL_EXTRA
        take_profit_1 = entry + TP1_POINTS
        take_profit_2 = entry + TP2_POINTS

        message = (
            "🟢 XAUUSD BUY SIGNAL\n\n"
            f"Entry: {entry:.2f}\n"
            f"Stop Loss: {stop_loss:.2f}\n"
            f"TP1: {take_profit_1:.2f}\n"
            f"TP2: {take_profit_2:.2f}\n\n"
            "Pattern: Hammer / Bullish Engulfing"
        )

    else:
        stop_loss = candle["high"] + SL_EXTRA
        take_profit_1 = entry - TP1_POINTS
        take_profit_2 = entry - TP2_POINTS

        message = (
            "🔴 XAUUSD SELL SIGNAL\n\n"
            f"Entry: {entry:.2f}\n"
            f"Stop Loss: {stop_loss:.2f}\n"
            f"TP1: {take_profit_1:.2f}\n"
            f"TP2: {take_profit_2:.2f}\n\n"
            "Pattern: Shooting Star / Bearish Engulfing"
        )

    tp1_hit = False
    trade_active = True
    state = "IN_TRADE"
    last_15m_time = candle["time"]

    print(message, flush=True)
    send_telegram(message)


# ==========================================
# 15M CONFIRMATION
# ==========================================

def check_15m_confirmation():
    global last_15m_time

    if state != "WAITING_15M":
        return

    candles = get_closed_candles("15m")

    if len(candles) < 2:
        return

    previous = candles[-2]
    current = candles[-1]

    candle_time = current["time"]

    if candle_time is None:
        return

    if last_15m_time == candle_time:
        return

    last_15m_time = candle_time

    hammer = is_hammer(current)
    shooting_star = is_shooting_star(current)

    bullish_engulfing = is_bullish_engulfing(
        previous,
        current
    )

    bearish_engulfing = is_bearish_engulfing(
        previous,
        current
    )

    print("=" * 35)
    print("NEW CLOSED 15M CANDLE")
    print("HAMMER:", hammer)
    print("SHOOTING STAR:", shooting_star)
    print("BULLISH ENGULFING:", bullish_engulfing)
    print("BEARISH ENGULFING:", bearish_engulfing)
    print("DIRECTION:", direction)
    print("=" * 35)

    if direction == "BUY":
        if hammer or bullish_engulfing:
            create_trade(current)
        else:
            print("NO BUY CONFIRMATION")

    elif direction == "SELL":
        if shooting_star or bearish_engulfing:
            create_trade(current)
        else:
            print("NO SELL CONFIRMATION")


# ==========================================
# TRADE MANAGEMENT
# ==========================================

def manage_trade():
    global stop_loss, tp1_hit, trade_active
    global state, weekly_wins, weekly_losses

    if not trade_active:
        return

    candles = get_closed_candles("15m")

    if not candles:
        return

    candle = candles[-1]

    if candle["time"] is None:
        return

    # ئەم کاندلەیە دەبێت دوای کاتی چوونە ناو ترێد بێت.
    if last_15m_time is not None:
        if candle["time"] <= last_15m_time:
            return

    high = candle["high"]
    low = candle["low"]

    # BUY management
    if direction == "BUY":

        # ئەگەر SL و TP هەردووکیان لە یەک کاندلدا
        # بگەن، بۆ پاراستنی هەژمارەکە SL پێشتر دادەنرێت.
        if low <= stop_loss:
            trade_active = False
            state = "WAITING_1H"
            weekly_losses += 1

            send_telegram(
                "❌ BUY STOP LOSS HIT\n\n"
                f"SL: {stop_loss:.2f}\n"
                "ترێدەکە کۆتایی هات."
            )
            return

        if not tp1_hit and high >= take_profit_1:
            tp1_hit = True
            stop_loss = entry

            send_telegram(
                "✅ BUY TP1 HIT\n\n"
                f"TP1: {take_profit_1:.2f}\n"
                f"Stop Loss گوازرایەوە بۆ Entry: {entry:.2f}"
            )

        if tp1_hit and high >= take_profit_2:
            trade_active = False
            state = "WAITING_1H"
            weekly_wins += 1

            send_telegram(
                "🏆 BUY TP2 HIT\n\n"
                f"TP2: {take_profit_2:.2f}\n"
                "ترێدەکە تەواو بوو."
            )
            return

    # SELL management
    elif direction == "SELL":

        if high >= stop_loss:
            trade_active = False
            state = "WAITING_1H"
            weekly_losses += 1

            send_telegram(
                "❌ SELL STOP LOSS HIT\n\n"
                f"SL: {stop_loss:.2f}\n"
                "ترێدەکە کۆتایی هات."
            )
            return

        if not tp1_hit and low <= take_profit_1:
            tp1_hit = True
            stop_loss = entry

            send_telegram(
                "✅ SELL TP1 HIT\n\n"
                f"TP1: {take_profit_1:.2f}\n"
                f"Stop Loss گوازرایەوە بۆ Entry: {entry:.2f}"
            )

        if tp1_hit and low <= take_profit_2:
            trade_active = False
            state = "WAITING_1H"
            weekly_wins += 1

            send_telegram(
                "🏆 SELL TP2 HIT\n\n"
                f"TP2: {take_profit_2:.2f}\n"
                "ترێدەکە تەواو بوو."
            )
            return


# ==========================================
# WEEKLY REPORT
# ==========================================

def weekly_report():
    global weekly_report_sent
    global weekly_wins, weekly_losses

    now = datetime.now(IRAQ_TZ)

    # Friday = 4
    if now.weekday() != 4:
        return

    if now.hour != 23:
        return

    report_key = now.strftime("%Y-%W")

    if weekly_report_sent == report_key:
        return

    total = weekly_wins + weekly_losses

    message = (
        "📊 XAUUSD WEEKLY REPORT\n\n"
        f"Completed Wins: {weekly_wins}\n"
        f"Completed Losses: {weekly_losses}\n"
        f"Total Completed Trades: {total}\n\n"
        "⚠️ ئەمە تەنها ژمارەی ترێدە تەواوبووەکانە."
    )

    send_telegram(message)

    weekly_report_sent = report_key
    weekly_wins = 0
    weekly_losses = 0


# ==========================================
# MAIN LOOP
# ==========================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing in Railway Variables"
        )

    if not CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing in Railway Variables"
        )

    print("XAUUSD TELEGRAM BOT STARTED", flush=True)

    # ئەگەر webhook چالاک بێت، polling ڕەنگە کار نەکات.
    try:
        webhook_response = requests.post(
            TELEGRAM_URL + "/deleteWebhook",
            data={"drop_pending_updates": False},
            timeout=10
        )

        print(
            "DELETE WEBHOOK:",
            webhook_response.status_code,
            webhook_response.text[:300],
            flush=True
        )

    except Exception as error:
        print("WEBHOOK CHECK ERROR:", repr(error))

    send_telegram(
        "🟢 XAUUSD BOT STARTED\n\n"
        "چاوەڕێی Support / Resistance ـە.\n"
        "نمونە: /support 4275 4300"
    )

    last_market_check = 0

    while True:
        try:
            # پەیامەکانی Telegram بەردەوام بپشکنە.
            check_telegram()

            now = time.time()

            if now - last_market_check >= CHECK_INTERVAL:
                last_market_check = now

                if support is None or resistance is None:
                    print(
                        "Waiting for Support / Resistance...",
                        flush=True
                    )

                else:
                    check_1h_breakout()
                    check_15m_confirmation()
                    manage_trade()

                weekly_report()

        except Exception as error:
            print("MAIN LOOP ERROR:", repr(error), flush=True)

        time.sleep(3)


if __name__ == "__main__":
    main()
