import os
import time
import requests
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, jsonify
# =====================================================
# CONFIGURATION
# =====================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
SYMBOL = os.getenv("SYMBOL", "XAUUSD").strip()
# Keep automatic trading disabled while testing.
AUTO_TRADE = os.getenv("AUTO_TRADE", "false").lower() == "true"
# Market-data URL used by the existing project.
MARKET_URL = "https://biquote.io/api/XAUUSD/ohlc"
BAGHDAD = ZoneInfo("Asia/Baghdad")
BREAKOUT_DISTANCE = 5.0
POLL_SECONDS = 15
# This bot version sends signals only.
# It does not submit broker orders.
app = Flask(__name__)
# =====================================================
# BOT STATE
# =====================================================
state_lock = threading.Lock()
support = None
resistance = None
state = "WAITING_SR"
direction = None
last_1h_candle = None
last_15m_candle = None
signal_sent = False
weekly_results = {
    "signals": 0,
    "buy_signals": 0,
    "sell_signals": 0,
}
# =====================================================
# TIME AND TELEGRAM
# =====================================================
def now_text():
    return datetime.now(BAGHDAD).strftime("%Y-%m-%d %H:%M:%S")
def telegram_request(method, payload=None):
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing", flush=True)
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        response = requests.post(
            url,
            json=payload or {},
            timeout=20
        )
        print(
            f"Telegram {method}: HTTP {response.status_code}",
            flush=True
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            print("Telegram API error:", data, flush=True)
            return None
        return data
    except Exception as exc:
        print("Telegram request error:", repr(exc), flush=True)
        return None
def send_message(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Cannot send Telegram message: credentials missing", flush=True)
        print(message, flush=True)
        return False
    result = telegram_request(
        "sendMessage",
        {
            "chat_id": CHAT_ID,
            "text": message,
        }
    )
    return result is not None
def get_updates(offset=None):
    if not BOT_TOKEN:
        return []
    params = {
        "timeout": 5,
    }
    if offset is not None:
        params["offset"] = offset
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params=params,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        if data.get("ok"):
            return data.get("result", [])
        print("Telegram getUpdates error:", data, flush=True)
    except Exception as exc:
        print("getUpdates error:", repr(exc), flush=True)
    return []
# =====================================================
# MARKET DATA
# =====================================================
def get_bars(timeframe):
    """
    Requests market data from the configured API.
    IMPORTANT:
    The exact API response schema must be verified.
    If the API does not return the expected structure,
    the bot will log an error and will not issue signals.
    """
    try:
        response = requests.get(
            MARKET_URL,
            params={"timeframe": timeframe},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            bars = data.get("bars")
            if bars is None:
                bars = data.get("data")
            if bars is None:
                bars = data.get("candles")
        elif isinstance(data, list):
            bars = data
        else:
            bars = None
        if not isinstance(bars, list):
            print(
                "MARKET DATA ERROR: Unrecognized API response.",
                "Timeframe:", timeframe,
                flush=True
            )
            return []
        normalized = []
        for candle in bars:
            if not isinstance(candle, dict):
                continue
            try:
                open_price = float(candle["open"])
                high_price = float(candle["high"])
                low_price = float(candle["low"])
                close_price = float(candle["close"])
                candle_id = (
                    candle.get("openTime")
                    or candle.get("time")
                    or candle.get("timestamp")
                    or candle.get("timeStamp")
                )
                if candle_id is None:
                    continue
                normalized.append({
                    "id": str(candle_id),
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price,
                    "raw": candle,
                })
            except (KeyError, TypeError, ValueError):
                continue
        normalized.sort(key=lambda item: item["id"])
        return normalized
    except Exception as exc:
        print(
            f"MARKET DATA ERROR ({timeframe}):",
            repr(exc),
            flush=True
        )
        return []
def get_latest_closed(timeframe):
    """
    Uses the last candle only if the API explicitly marks it closed.
    Does not guess that a candle is closed if the API gives no status.
    """
    bars = get_bars(timeframe)
    if not bars:
        return None
    for candle in reversed(bars):
        raw = candle["raw"]
        closed_value = raw.get("isClosed")
        if closed_value is None:
            closed_value = raw.get("is_closed")
        if closed_value is None:
            is_open_value = raw.get("isOpen")
            if is_open_value is not None:
                if str(is_open_value).lower() in ("false", "0"):
                    return candle
            continue
        if str(closed_value).lower() in ("true", "1"):
            return candle
    print(
        f"No confirmed closed candle found for {timeframe}.",
        flush=True
    )
    return None
# =====================================================
# SUPPORT AND RESISTANCE
# =====================================================
def set_support_resistance(new_support, new_resistance):
    global support, resistance
    global state, direction
    global last_1h_candle, last_15m_candle
    global signal_sent
    if new_support <= 0 or new_resistance <= 0:
        return False, "Support and resistance must be positive."
    if new_support >= new_resistance:
        return False, "Support must be lower than resistance."
    with state_lock:
        support = float(new_support)
        resistance = float(new_resistance)
        state = "WAITING_1H"
        direction = None
        signal_sent = False
        candle_1h = get_latest_closed("1h")
        candle_15m = get_latest_closed("15m")
        last_1h_candle = candle_1h["id"] if candle_1h else None
        last_15m_candle = candle_15m["id"] if candle_15m else None
    message = (
        "✅ SUPPORT / RESISTANCE UPDATED\n\n"
        f"🟦 Support: {support:.2f}\n"
        f"🟥 Resistance: {resistance:.2f}\n"
        "🤖 State: WAITING_1H"
    )
    send_message(message)
    return True, message
# =====================================================
# CANDLE PATTERNS
# =====================================================
def candle_parts(candle):
    body = abs(candle["close"] - candle["open"])
    full_range = candle["high"] - candle["low"]
    upper_wick = candle["high"] - max(
        candle["open"], candle["close"]
    )
    lower_wick = min(
        candle["open"], candle["close"]
    ) - candle["low"]
    return body, full_range, upper_wick, lower_wick
def is_hammer(candle):
    body, full_range, upper_wick, lower_wick = candle_parts(candle)
    if full_range <= 0:
        return False
    if body <= 0:
        body = full_range * 0.02
    return (
        lower_wick >= body * 2
        and upper_wick <= body
        and max(candle["open"], candle["close"])
        >= candle["low"] + full_range * 0.55
    )
def is_shooting_star(candle):
    body, full_range, upper_wick, lower_wick = candle_parts(candle)
    if full_range <= 0:
        return False
    if body <= 0:
        body = full_range * 0.02
    return (
        upper_wick >= body * 2
        and lower_wick <= body
        and min(candle["open"], candle["close"])
        <= candle["low"] + full_range * 0.45
    )
def is_bullish_engulfing(previous, current):
    previous_bearish = previous["close"] < previous["open"]
    current_bullish = current["close"] > current["open"]
    return (
        previous_bearish
        and current_bullish
        and current["open"] <= previous["close"]
        and current["close"] >= previous["open"]
    )
def is_bearish_engulfing(previous, current):
    previous_bullish = previous["close"] > previous["open"]
    current_bearish = current["close"] < current["open"]
    return (
        previous_bullish
        and current_bearish
        and current["open"] >= previous["close"]
        and current["close"] <= previous["open"]
    )
# =====================================================
# 1H DIRECTION CONFIRMATION
# =====================================================
def check_1h():
    global last_1h_candle, state, direction
    global last_15m_candle
    candle = get_latest_closed("1h")
    if candle is None:
        return
    if candle["id"] == last_1h_candle:
        return
    last_1h_candle = candle["id"]
    close_price = candle["close"]
    print(
        "NEW CLOSED 1H CANDLE:",
        close_price,
        flush=True
    )
    if resistance is not None and close_price >= resistance + BREAKOUT_DISTANCE:
        direction = "BUY"
        state = "WAITING_BUY_15M"
        latest_15m = get_latest_closed("15m")
        last_15m_candle = latest_15m["id"] if latest_15m else None
        send_message(
            "🟢 XAUUSD 1H BUY DIRECTION CONFIRMED\n\n"
            f"1H Close: {close_price:.2f}\n"
            f"Resistance: {resistance:.2f}\n\n"
            "Wait for a closed 15M Hammer or Bullish Engulfing candle."
        )
        return
    if support is not None and close_price <= support - BREAKOUT_DISTANCE:
        direction = "SELL"
        state = "WAITING_SELL_15M"
        latest_15m = get_latest_closed("15m")
        last_15m_candle = latest_15m["id"] if latest_15m else None
        send_message(
            "🔴 XAUUSD 1H SELL DIRECTION CONFIRMED\n\n"
            f"1H Close: {close_price:.2f}\n"
            f"Support: {support:.2f}\n\n"
            "Wait for a closed 15M Shooting Star or Bearish Engulfing candle."
        )
        return
    print(
        "NO 1H BREAKOUT:",
        f"Close={close_price:.2f}",
        flush=True
    )
# =====================================================
# 15M CONFIRMATION AND SIGNAL
# =====================================================
def check_confirmation(expected_direction):
    global last_15m_candle, state, signal_sent
    global weekly_results
    bars = get_bars("15m")
    if len(bars) < 2:
        return
    # The latest bar must be confirmed closed by the API.
    current = get_latest_closed("15m")
    if current is None:
        return
    if current["id"] == last_15m_candle:
        return
    # Find the preceding candle in the returned sequence.
    current_index = None
    for index, bar in enumerate(bars):
        if bar["id"] == current["id"]:
            current_index = index
            break
    if current_index is None or current_index == 0:
        last_15m_candle = current["id"]
        return
    previous = bars[current_index - 1]
    last_15m_candle = current["id"]
    if expected_direction == "BUY":
        hammer = is_hammer(current)
        engulfing = is_bullish_engulfing(previous, current)
        if not (hammer or engulfing):
            return
        pattern = "Hammer" if hammer else "Bullish Engulfing"
        entry = current["close"]
        stop_loss = current["low"] - 0.10
        risk = entry - stop_loss
        if risk <= 0:
            return
        tp1 = entry + risk
        tp2 = entry + risk * 1.5
        send_message(
            "🟢 XAUUSD BUY SIGNAL\n\n"
            f"Pattern: {pattern}\n"
            f"Entry: {entry:.2f}\n"
            f"Stop Loss: {stop_loss:.2f}\n"
            f"TP1: {tp1:.2f}\n"
            f"TP2: {tp2:.2f}\n\n"
            "⚠️ Signal only. No automatic order was placed."
        )
        weekly_results["signals"] += 1
        weekly_results["buy_signals"] += 1
        signal_sent = True
        state = "SIGNAL_SENT"
    elif expected_direction == "SELL":
        shooting_star = is_shooting_star(current)
        engulfing = is_bearish_engulfing(previous, current)
        if not (shooting_star or engulfing):
            return
        pattern = (
            "Shooting Star"
            if shooting_star
            else "Bearish Engulfing"
        )
        entry = current["close"]
        stop_loss = current["high"] + 0.10
        risk = stop_loss - entry
        if risk <= 0:
            return
        tp1 = entry - risk
        tp2 = entry - risk * 1.5
        send_message(
            "🔴 XAUUSD SELL SIGNAL\n\n"
            f"Pattern: {pattern}\n"
            f"Entry: {entry:.2f}\n"
            f"Stop Loss: {stop_loss:.2f}\n"
            f"TP1: {tp1:.2f}\n"
            f"TP2: {tp2:.2f}\n\n"
            "⚠️ Signal only. No automatic order was placed."
        )
        weekly_results["signals"] += 1
        weekly_results["sell_signals"] += 1
        signal_sent = True
        state = "SIGNAL_SENT"
# =====================================================
# TELEGRAM COMMANDS
# =====================================================
def process_command(text):
    global state, support, resistance
    global direction, signal_sent
    parts = text.strip().split()
    if not parts:
        return
    command = parts[0].split("@")[0].lower()
    if command == "/start":
        send_message(
            "🤖 XAUUSD BOT IS ONLINE\n\n"
            "Send Support and Resistance like this:\n"
            "/support 4170 4200\n\n"
            "Other commands:\n"
            "/status\n"
            "/cancel\n"
            "/help"
        )
    elif command == "/help":
        send_message(
            "📘 XAUUSD BOT HELP\n\n"
            "/support 4170 4200\n"
            "First number = Support\n"
            "Second number = Resistance\n\n"
            "/status - Show current state\n"
            "/cancel - Clear current levels\n"
            "/help - Show this help"
        )
    elif command == "/support":
        if len(parts) != 3:
            send_message(
                "❌ Wrong format.\n"
                "Use: /support 4170 4200"
            )
            return
        try:
            new_support = float(parts[1])
            new_resistance = float(parts[2])
        except ValueError:
            send_message("❌ Please enter valid numbers.")
            return
        ok, message = set_support_resistance(
            new_support,
            new_resistance
        )
        if not ok:
            send_message("❌ " + message)
    elif command == "/status":
        send_message(
            "📊 XAUUSD BOT STATUS\n\n"
            f"Symbol: {SYMBOL}\n"
            f"Support: {support if support is not None else 'Not set'}\n"
            f"Resistance: {resistance if resistance is not None else 'Not set'}\n"
            f"Direction: {direction or 'Not confirmed'}\n"
            f"State: {state}\n"
            f"Auto trading: {AUTO_TRADE}\n"
            f"Time: {now_text()}"
        )
    elif command == "/cancel":
        with state_lock:
            support = None
            resistance = None
            direction = None
            state = "WAITING_SR"
            signal_sent = False
        send_message(
            "🛑 Current Support / Resistance cancelled.\n\n"
            "Waiting for new Support / Resistance."
        )
def check_telegram():
    if not BOT_TOKEN or not CHAT_ID:
        return
    # Save the update offset so messages are not repeatedly processed
    # within the current process.
    global telegram_offset
    updates = get_updates(telegram_offset)
    for update in updates:
        telegram_offset = update["update_id"] + 1
        message = update.get("message")
        if not message:
            continue
        chat = message.get("chat", {})
        incoming_chat_id = str(chat.get("id", ""))
        if incoming_chat_id != str(CHAT_ID):
            continue
        text = message.get("text", "")
        if text.startswith("/"):
            process_command(text)
telegram_offset = None
# =====================================================
# WEEKLY REPORT
# =====================================================
def check_weekly_report():
    """
    Basic weekly signal-count report.
    This is not a broker profit/loss report.
    """
    now = datetime.now(BAGHDAD)
    if now.weekday() != 6:
        return
    if now.hour != 20 or now.minute >= 15:
        return
    report_key = now.strftime("%Y-%W")
    global last_weekly_report_key
    if report_key == last_weekly_report_key:
        return
    last_weekly_report_key = report_key
    send_message(
        "📊 XAUUSD WEEKLY SIGNAL REPORT\n\n"
        f"Total signals: {weekly_results['signals']}\n"
        f"Buy signals: {weekly_results['buy_signals']}\n"
        f"Sell signals: {weekly_results['sell_signals']}\n\n"
        "Note: This report counts signals, not realized profit."
    )
last_weekly_report_key = None
# =====================================================
# HEALTH CHECK
# =====================================================
@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "symbol": SYMBOL,
        "state": state,
        "support": support,
        "resistance": resistance,
        "direction": direction,
        "auto_trade": AUTO_TRADE,
        "time": now_text(),
    })
@app.route("/health")
def health():
    return jsonify({"status": "ok"})
def run_bot_loop():
    print("XAUUSD BOT STARTED", flush=True)
    print("Timezone: Asia/Baghdad", flush=True)
    print("AUTO_TRADE:", AUTO_TRADE, flush=True)
    print(
        "BOT_TOKEN configured:",
        bool(BOT_TOKEN),
        flush=True
    )
    print(
        "CHAT_ID configured:",
        bool(CHAT_ID),
        flush=True
    )
    if not BOT_TOKEN or not CHAT_ID:
        print(
            "WARNING: Set BOT_TOKEN and CHAT_ID in Railway Variables.",
            flush=True
        )
    while True:
        try:
            check_telegram()
            check_weekly_report()
            if support is None or resistance is None:
                time.sleep(POLL_SECONDS)
                continue
            if state == "WAITING_1H":
                check_1h()
            elif state == "WAITING_BUY_15M":
                check_confirmation("BUY")
            elif state == "WAITING_SELL_15M":
                check_confirmation("SELL")
            # After one signal, wait for new Support / Resistance.
            elif state == "SIGNAL_SENT":
                pass
            time.sleep(POLL_SECONDS)
        except Exception as exc:
            print(
                "MAIN LOOP ERROR:",
                repr(exc),
                flush=True
            )
            time.sleep(POLL_SECONDS)
# =====================================================
# START
# =====================================================
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    server_thread = threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=port,
            use_reloader=False
        ),
        daemon=True
    )
    server_thread.start()
    run_bot_loop()
