import os
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
# =========================================================
# SETTINGS
# =========================================================
URL = "https://biquote.io/api/XAUUSD/ohlc"
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
IRAQ_TZ = ZoneInfo("Asia/Baghdad")
POLL_SECONDS = 15
BREAKOUT_DISTANCE = 5.0
STOP_BUFFER = 3.0
TP1_DISTANCE = 10.0
TP2_DISTANCE = 15.0
session = requests.Session()
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
tp1_candle_time = None
current_trade = None
weekly_results = {
    "Monday": [],
    "Tuesday": [],
    "Wednesday": [],
    "Thursday": [],
    "Friday": []
}
last_report_week = None
# =========================================================
# TIME
# =========================================================
def iraq_now():
    return datetime.now(IRAQ_TZ)
def today_name():
    return iraq_now().strftime("%A")
# =========================================================
# TELEGRAM
# =========================================================
def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: Telegram variables are missing.")
        return False
    try:
        response = session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": message
            },
            timeout=15
        )
        result = response.json()
        if response.ok and result.get("ok"):
            print("Telegram message sent.")
            return True
        print("Telegram ERROR:", result)
        return False
    except Exception as e:
        print("Telegram ERROR:", e)
        return False
# =========================================================
# CANDLE PATTERNS
# =========================================================
def is_hammer(o, h, l, c):
    body = abs(c - o)
    if body == 0:
        return False
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return (
        lower_wick >= body * 2
        and upper_wick <= body
    )
def is_shooting_star(o, h, l, c):
    body = abs(c - o)
    if body == 0:
        return False
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return (
        upper_wick >= body * 2
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
# MARKET DATA
# =========================================================
def get_bars(interval, limit=10):
    try:
        response = session.get(
            URL,
            params={
                "interval": interval,
                "limit": limit
            },
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            print("Market ERROR: Unexpected response.")
            return []
        bars = data.get("bars", [])
        if not isinstance(bars, list):
            print("Market ERROR: bars is not a list.")
            return []
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
def get_latest_monitoring_candle():
    """
    Prefer the currently open 15-minute candle.
    Its high/low must update while the candle is forming
    for intrabar target monitoring to work.
    If no open candle is returned, use the newest
    available candle as a fallback.
    """
    bars = get_bars("15m")
    if not bars:
        return None
    for candle in bars:
        if candle.get("isOpen") is True:
            return candle
    # Assumes newest candles are returned first.
    return bars[0]
def get_two_closed_15m():
    bars = get_bars("15m")
    closed = []
    for candle in bars:
        if candle.get("isOpen") is False:
            closed.append(candle)
        if len(closed) == 2:
            break
    if len(closed) < 2:
        return None, None
    # Assumes newest candles are returned first.
    return closed[0], closed[1]
def candle_time(candle):
    return str(candle.get("openTime", ""))
def candle_prices(candle):
    return (
        float(candle["open"]),
        float(candle["high"]),
        float(candle["low"]),
        float(candle["close"])
    )
# =========================================================
# TRADE RECORDS
# =========================================================
def start_trade_record():
    global current_trade
    current_trade = {
        "day": today_name(),
        "profit": 0,
        "stop_loss": 0,
        "status": "OPEN"
    }
def record_result(profit, loss, status):
    global current_trade
    if current_trade is None:
        start_trade_record()
    day = current_trade["day"]
    if day in weekly_results:
        weekly_results[day].append({
            "profit": profit,
            "stop_loss": loss,
            "status": status
        })
    print("TRADE RESULT:", status)
    print("Profit:", profit, "Pips")
    print("Stop Loss:", loss, "Pips")
    current_trade = None
def finish_trade_tp1():
    record_result(100, 0, "TP1")
def finish_trade_tp2():
    record_result(150, 0, "TP2")
def finish_trade_sl():
    global entry, stop_loss
    difference = abs(entry - stop_loss)
    sl_pips = round(difference * 10)
    record_result(0, sl_pips, "SL")
def finish_trade_breakeven():
    record_result(100, 0, "TP1_BREAKEVEN")
# =========================================================
# RESET
# =========================================================
def reset_trade():
    global direction, entry, stop_loss, tp1, tp2
    global tp1_hit, tp1_candle_time, current_trade
    global last_manage_15m_time
    direction = None
    entry = None
    stop_loss = None
    tp1 = None
    tp2 = None
    tp1_hit = False
    tp1_candle_time = None
    current_trade = None
    last_manage_15m_time = None
def reset_everything():
    global support, resistance, state
    global last_1h_time, last_15m_time
    support = None
    resistance = None
    state = "WAITING_SUPPORT_RESISTANCE"
    last_1h_time = None
    last_15m_time = None
    reset_trade()
    print("Waiting for new Support / Resistance.")
# =========================================================
# SUPPORT / RESISTANCE
# =========================================================
def set_support_resistance(new_support, new_resistance):
    global support, resistance, state
    global last_1h_time, last_15m_time
    if new_support >= new_resistance:
        send_telegram(
            "❌ Support must be below Resistance."
        )
        return
    support = new_support
    resistance = new_resistance
    reset_trade()
    state = "WAITING_1H"
    current_1h = get_latest_closed("1h")
    current_15m = get_latest_closed("15m")
    last_1h_time = (
        candle_time(current_1h)
        if current_1h else None
    )
    last_15m_time = (
        candle_time(current_15m)
        if current_15m else None
    )
    print("NEW SUPPORT / RESISTANCE")
    print("Support:", support)
    print("Resistance:", resistance)
    print("State:", state)
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
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        response = session.get(
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
            print("Telegram getUpdates ERROR:", data)
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
            if text.startswith("/support"):
                parts = text.split()
                if len(parts) != 3:
                    send_telegram(
                        "❌ Wrong format.\n\n"
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
                        "❌ Invalid prices.\n"
                        "Use: /support 4275 4300"
                    )
            elif text == "/cancel":
                reset_everything()
                send_telegram(
                    "❌ SUPPORT / RESISTANCE CANCELLED\n\n"
                    "⏳ Waiting for new Support / Resistance.\n\n"
                    "Use:\n/support 4275 4300"
                )
            elif text == "/status":
                if support is None:
                    send_telegram(
                        "⚠️ Support / Resistance not set.\n\n"
                        "Use: /support 4275 4300"
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
# 1H BREAKOUT
# =========================================================
def check_1h():
    global state, last_1h_time, last_15m_time
    candle = get_latest_closed("1h")
    if candle is None:
        return
    candle_id = candle_time(candle)
    if not candle_id or candle_id == last_1h_time:
        return
    last_1h_time = candle_id
    close_price = float(candle["close"])
    print("NEW CLOSED 1H:", close_price)
    if close_price >= resistance + BREAKOUT_DISTANCE:
        state = "WAITING_BUY_15M"
        current = get_latest_closed("15m")
        last_15m_time = (
            candle_time(current) if current else None
        )
        send_telegram(
            "🟢 CONFIRMATION FOR TODAY: BUY\n\n"
            "1H breakout confirmed. Wait for Hammer "
            "or Bullish Engulfing on a CLOSED 15M candle."
        )
        return
    if close_price <= support - BREAKOUT_DISTANCE:
        state = "WAITING_SELL_15M"
        current = get_latest_closed("15m")
        last_15m_time = (
            candle_time(current) if current else None
        )
        send_telegram(
            "🔴 CONFIRMATION FOR TODAY: SELL\n\n"
            "1H breakout confirmed. Wait for Shooting Star "
            "or Bearish Engulfing on a CLOSED 15M candle."
        )
        return
    print("NO 1H BREAKOUT")
# =========================================================
# BUY 15M CONFIRMATION
# =========================================================
def check_buy_confirmation():
    global state, last_15m_time
    global direction, entry, stop_loss, tp1, tp2
    global tp1_hit, tp1_candle_time, last_manage_15m_time
    current, previous = get_two_closed_15m()
    if current is None or previous is None:
        return
    current_id = candle_time(current)
    if not current_id or current_id == last_15m_time:
        return
    last_15m_time = current_id
    o, h, l, c = candle_prices(current)
    po, _, _, pc = candle_prices(previous)
    hammer = is_hammer(o, h, l, c)
    engulfing = is_bullish_engulfing(po, pc, o, c)
    if not (hammer or engulfing):
        return
    direction = "BUY"
    entry = c
    stop_loss = l - STOP_BUFFER
    tp1 = entry + TP1_DISTANCE
    tp2 = entry + TP2_DISTANCE
    tp1_hit = False
    tp1_candle_time = None
    last_manage_15m_time = current_id
    state = "MANAGING_BUY"
    start_trade_record()
    pattern = (
        "Hammer" if hammer else "Bullish Engulfing"
    )
    print(
        "BUY SIGNAL",
        pattern,
        entry,
        stop_loss,
        tp1,
        tp2
    )
    send_telegram(
        "🟢 XAUUSD BUY SIGNAL\n\n"
        f"Pattern: {pattern}\n"
        f"Entry: {entry:.2f}\n"
        f"Stop Loss: {stop_loss:.2f}\n"
        f"TP1: {tp1:.2f}\n"
        f"TP2: {tp2:.2f}"
    )
# =========================================================
# SELL 15M CONFIRMATION
# =========================================================
def check_sell_confirmation():
    global state, last_15m_time
    global direction, entry, stop_loss, tp1, tp2
    global tp1_hit, tp1_candle_time, last_manage_15m_time
    current, previous = get_two_closed_15m()
    if current is None or previous is None:
        return
    current_id = candle_time(current)
    if not current_id or current_id == last_15m_time:
        return
    last_15m_time = current_id
    o, h, l, c = candle_prices(current)
    po, _, _, pc = candle_prices(previous)
    shooting = is_shooting_star(o, h, l, c)
    engulfing = is_bearish_engulfing(po, pc, o, c)
    if not (shooting or engulfing):
        return
    direction = "SELL"
    entry = c
    stop_loss = h + STOP_BUFFER
    tp1 = entry - TP1_DISTANCE
    tp2 = entry - TP2_DISTANCE
    tp1_hit = False
    tp1_candle_time = None
    last_manage_15m_time = current_id
    state = "MANAGING_SELL"
    start_trade_record()
    pattern = (
        "Shooting Star" if shooting else "Bearish Engulfing"
    )
    print(
        "SELL SIGNAL",
        pattern,
        entry,
        stop_loss,
        tp1,
        tp2
    )
    send_telegram(
        "🔴 XAUUSD SELL SIGNAL\n\n"
        f"Pattern: {pattern}\n"
        f"Entry: {entry:.2f}\n"
        f"Stop Loss: {stop_loss:.2f}\n"
        f"TP1: {tp1:.2f}\n"
        f"TP2: {tp2:.2f}"
    )
# =========================================================
# FAST BUY TRADE MONITOR
# =========================================================
def manage_buy():
    global tp1_hit, stop_loss, tp1_candle_time
    candle = get_latest_monitoring_candle()
    if candle is None:
        return
    candle_id = candle_time(candle)
    try:
        _, high, low, close = candle_prices(candle)
    except (KeyError, TypeError, ValueError) as e:
        print("BUY MONITOR DATA ERROR:", e)
        return
    print(
        f"BUY MONITOR | Price: {close:.2f} "
        f"| TP1: {tp1:.2f} | TP2: {tp2:.2f} "
        f"| SL: {stop_loss:.2f}"
    )
    # Before TP1, conservatively check SL first.
    if not tp1_hit:
        if low <= stop_loss:
            finish_trade_sl()
            send_telegram(
                "⛔ BUY SIGNAL: STOP LOSS REACHED\n\n"
                f"Entry: {entry:.2f}\n"
                f"Stop Loss: {stop_loss:.2f}\n\n"
                "The signal is finished.\n"
                "Waiting for new Support / Resistance."
            )
            reset_everything()
            return
        if high >= tp1:
            tp1_hit = True
            stop_loss = entry
            tp1_candle_time = candle_id
            send_telegram(
                "🎉 BUY TP1 REACHED\n\n"
                "Target One — 100 PIPS\n"
                "Stop Loss moved to Breakeven.\n\n"
                f"Entry: {entry:.2f}\n"
                f"New Stop Loss: {entry:.2f}\n"
                f"TP2: {tp2:.2f}\n\n"
                "Monitoring continues every 15 seconds."
            )
            # Check again on the next polling cycle.
            return
    else:
        # If TP1 and TP2 are both reached during this
        # candle, TP2 is the farther BUY target.
        if high >= tp2:
            finish_trade_tp2()
            send_telegram(
                "🏆 BUY TP2 REACHED\n\n"
                "Target Two — 150 PIPS\n"
                f"Entry: {entry:.2f}\n"
                f"TP2: {tp2:.2f}\n\n"
                "Signal monitoring finished.\n"
                "Waiting for new Support / Resistance."
            )
            reset_everything()
            return
        # Do not use the old part of the TP1 candle
        # to decide whether breakeven was hit.
        if candle_id == tp1_candle_time:
            return
        if low <= entry:
            finish_trade_breakeven()
            send_telegram(
                "⚠️ BUY BREAKEVEN REACHED\n\n"
                "Target One: 100 PIPS was reached first.\n"
                "Price returned to Entry.\n\n"
                "Waiting for new Support / Resistance."
            )
            reset_everything()
            return
# =========================================================
# FAST SELL TRADE MONITOR
# =========================================================
def manage_sell():
    global tp1_hit, stop_loss, tp1_candle_time
    candle = get_latest_monitoring_candle()
    if candle is None:
        return
    candle_id = candle_time(candle)
    try:
        _, high, low, close = candle_prices(candle)
    except (KeyError, TypeError, ValueError) as e:
        print("SELL MONITOR DATA ERROR:", e)
        return
    print(
        f"SELL MONITOR | Price: {close:.2f} "
        f"| TP1: {tp1:.2f} | TP2: {tp2:.2f} "
        f"| SL: {stop_loss:.2f}"
    )
    # Before TP1, conservatively check SL first.
    if not tp1_hit:
        if high >= stop_loss:
            finish_trade_sl()
            send_telegram(
                "⛔ SELL SIGNAL: STOP LOSS REACHED\n\n"
                f"Entry: {entry:.2f}\n"
                f"Stop Loss: {stop_loss:.2f}\n\n"
                "The signal is finished.\n"
                "Waiting for new Support / Resistance."
            )
            reset_everything()
            return
        if low <= tp1:
            tp1_hit = True
            stop_loss = entry
            tp1_candle_time = candle_id
            send_telegram(
                "🎉 SELL TP1 REACHED\n\n"
                "Target One — 100 PIPS\n"
                "Stop Loss moved to Breakeven.\n\n"
                f"Entry: {entry:.2f}\n"
                f"New Stop Loss: {entry:.2f}\n"
                f"TP2: {tp2:.2f}\n\n"
                "Monitoring continues every 15 seconds."
            )
            # Check again on the next polling cycle.
            return
    else:
        # If TP1 and TP2 are both reached during this
        # candle, TP2 is the farther SELL target.
        if low <= tp2:
            finish_trade_tp2()
            send_telegram(
                "🏆 SELL TP2 REACHED\n\n"
                "Target Two — 150 PIPS\n"
                f"Entry: {entry:.2f}\n"
                f"TP2: {tp2:.2f}\n\n"
                "Signal monitoring finished.\n"
                "Waiting for new Support / Resistance."
            )
            reset_everything()
            return
        # Avoid using earlier price movement in the
        # same TP1 candle to trigger breakeven.
        if candle_id == tp1_candle_time:
            return
        if high >= entry:
            finish_trade_breakeven()
            send_telegram(
                "⚠️ SELL BREAKEVEN REACHED\n\n"
                "Target One: 100 PIPS was reached first.\n"
                "Price returned to Entry.\n\n"
                "Waiting for new Support / Resistance."
            )
            reset_everything()
            return
# =========================================================
# WEEKLY REPORT
# =========================================================
def make_weekly_report():
    total_profit = 0
    total_stop = 0
    lines = [
        "📊 XAUUSD WEEKLY SIGNAL RESULTS",
        ""
    ]
    days = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday"
    ]
    for day in days:
        lines.append(day)
        trades = weekly_results[day]
        if not trades:
            lines.append("No completed signals")
            lines.append("")
            continue
        for index, trade in enumerate(trades, start=1):
            lines.append(
                f"Signal {index}: {trade['status']}"
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
    lines.extend([
        "━━━━━━━━━━━━━━",
        f"Total Take Profit: {total_profit} Pips",
        f"Total Stop Loss: {total_stop} Pips",
        "",
        "Results are simulated signal tracking, "
        "not broker records."
    ])
    return "\n".join(lines)
def check_weekly_report():
    global last_report_week, weekly_results
    now = iraq_now()
    # Friday at or after 23:00 Iraq time.
    if now.weekday() != 4 or now.hour < 23:
        return
    current_week = now.strftime("%Y-%W")
    if last_report_week == current_week:
        return
    report = make_weekly_report()
    if send_telegram(report):
        last_report_week = current_week
        weekly_results = {
            "Monday": [],
            "Tuesday": [],
            "Wednesday": [],
            "Thursday": [],
            "Friday": []
        }
        print("WEEKLY REPORT SENT")
# =========================================================
# STARTUP
# =========================================================
print("================================")
print("XAUUSD TELEGRAM SIGNAL BOT")
print("Signal-only mode: NO ORDERS")
print("Time zone: Asia/Baghdad")
print("Target monitoring: every 15 seconds")
print("================================")
if not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError(
        "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
        "in Railway Variables."
    )
# =========================================================
# MAIN LOOP
# =========================================================
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
            check_buy_confirmation()
        elif state == "WAITING_SELL_15M":
            check_sell_confirmation()
        elif state == "MANAGING_BUY":
            manage_buy()
        elif state == "MANAGING_SELL":
            manage_sell()
        time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("Bot stopped.")
        break
    except Exception as e:
        print("MAIN ERROR:", repr(e))
        time.sleep(POLL_SECONDS)
