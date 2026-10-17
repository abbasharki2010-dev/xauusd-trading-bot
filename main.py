import os
import json
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
# Default file works for process restarts in the same container.
# For persistence across Railway redeploys, attach a Railway Volume
# and set BOT_DATA_FILE=/data/xauusd_bot_state.json.
DATA_FILE = os.getenv("BOT_DATA_FILE", "bot_state.json")
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
session = requests.Session()

# =========================================================
# DEFAULT STATE
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
weekly_results = {day: [] for day in DAYS}
last_report_week = None

# =========================================================
# TIME AND PERSISTENCE
# =========================================================
def iraq_now():
    return datetime.now(IRAQ_TZ)


def today_name():
    return iraq_now().strftime("%A")


def save_state():
    """Persist bot state. A Railway Volume is needed across redeploys."""
    payload = {
        "support": support,
        "resistance": resistance,
        "state": state,
        "last_1h_time": last_1h_time,
        "last_15m_time": last_15m_time,
        "last_manage_15m_time": last_manage_15m_time,
        "last_update_id": last_update_id,
        "direction": direction,
        "entry": entry,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "tp1_hit": tp1_hit,
        "tp1_candle_time": tp1_candle_time,
        "current_trade": current_trade,
        "weekly_results": weekly_results,
        "last_report_week": last_report_week,
    }
    try:
        folder = os.path.dirname(os.path.abspath(DATA_FILE))
        os.makedirs(folder, exist_ok=True)
        temp_file = DATA_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        os.replace(temp_file, DATA_FILE)
    except Exception as exc:
        print("STATE SAVE ERROR:", repr(exc))


def load_state():
    global support, resistance, state
    global last_1h_time, last_15m_time, last_manage_15m_time
    global last_update_id, direction, entry, stop_loss, tp1, tp2
    global tp1_hit, tp1_candle_time, current_trade
    global weekly_results, last_report_week
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            saved = json.load(file)
        support = saved.get("support")
        resistance = saved.get("resistance")
        state = saved.get("state", "WAITING_SUPPORT_RESISTANCE")
        last_1h_time = saved.get("last_1h_time")
        last_15m_time = saved.get("last_15m_time")
        last_manage_15m_time = saved.get("last_manage_15m_time")
        last_update_id = int(saved.get("last_update_id", 0))
        direction = saved.get("direction")
        entry = saved.get("entry")
        stop_loss = saved.get("stop_loss")
        tp1 = saved.get("tp1")
        tp2 = saved.get("tp2")
        tp1_hit = bool(saved.get("tp1_hit", False))
        tp1_candle_time = saved.get("tp1_candle_time")
        current_trade = saved.get("current_trade")
        loaded_results = saved.get("weekly_results", {})
        weekly_results = {
            day: loaded_results.get(day, []) if isinstance(loaded_results, dict) else []
            for day in DAYS
        }
        last_report_week = saved.get("last_report_week")
        print("Saved bot state loaded from", DATA_FILE)
    except FileNotFoundError:
        print("No saved state found; starting fresh.")
    except Exception as exc:
        print("STATE LOAD ERROR:", repr(exc))

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
            data={"chat_id": CHAT_ID, "text": message},
            timeout=15,
        )
        try:
            result = response.json()
        except ValueError:
            result = {"description": response.text[:500]}
        if response.ok and result.get("ok"):
            print("Telegram message sent.")
            return True
        print("Telegram ERROR:", result)
        return False
    except Exception as exc:
        print("Telegram ERROR:", repr(exc))
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
    return lower_wick >= body * 2 and upper_wick <= body


def is_shooting_star(o, h, l, c):
    body = abs(c - o)
    if body == 0:
        return False
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return upper_wick >= body * 2 and lower_wick <= body


def is_bullish_engulfing(po, pc, co, cc):
    return pc < po and cc > co and co <= pc and cc >= po


def is_bearish_engulfing(po, pc, co, cc):
    return pc > po and cc < co and co >= pc and cc <= po

# =========================================================
# MARKET DATA
# =========================================================
def get_bars(interval, limit=10):
    try:
        response = session.get(
            URL,
            params={"interval": interval, "limit": limit},
            timeout=15,
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
    except Exception as exc:
        print("Market ERROR:", repr(exc))
        return []


def get_latest_closed(interval):
    for candle in get_bars(interval):
        if candle.get("isOpen") is False:
            return candle
    return None


def get_latest_monitoring_candle():
    bars = get_bars("15m")
    if not bars:
        return None
    for candle in bars:
        if candle.get("isOpen") is True:
            return candle
    # Fallback assumes the API returns newest candles first.
    return bars[0]


def get_two_closed_15m():
    closed = []
    for candle in get_bars("15m"):
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
        float(candle["close"]),
    )

# =========================================================
# TRADE RECORDS AND PIP CALCULATION
# =========================================================
def start_trade_record():
    global current_trade
    current_trade = {
        "day": today_name(),
        "profit": 0,
        "stop_loss": 0,
        "status": "OPEN",
        "entry": entry,
        "direction": direction,
        "started_at": iraq_now().isoformat(),
    }
    save_state()


def record_result(profit, loss, status):
    global current_trade
    if current_trade is None:
        start_trade_record()
    day = current_trade.get("day", today_name())
    result = {
        "profit": round(float(profit), 1),
        "stop_loss": round(float(loss), 1),
        "status": status,
        "direction": current_trade.get("direction", direction),
        "entry": current_trade.get("entry", entry),
        "finished_at": iraq_now().isoformat(),
    }
    if day in weekly_results:
        weekly_results[day].append(result)
    print("TRADE RESULT:", status)
    print("Profit:", result["profit"], "Pips")
    print("Stop Loss:", result["stop_loss"], "Pips")
    current_trade = None
    save_state()


def finish_trade_tp2():
    record_result(150, 0, "TP2")


def finish_trade_sl():
    global entry, stop_loss
    if entry is None or stop_loss is None:
        loss_pips = 0
    else:
        loss_pips = round(abs(float(entry) - float(stop_loss)) * 10, 1)
    record_result(0, loss_pips, "SL")


def finish_trade_breakeven():
    # TP1 was reached first, so this outcome records +100 pips.
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
    save_state()


def reset_everything():
    global support, resistance, state, last_1h_time, last_15m_time
    support = None
    resistance = None
    state = "WAITING_SUPPORT_RESISTANCE"
    last_1h_time = None
    last_15m_time = None
    reset_trade()
    save_state()
    print("Waiting for new Support / Resistance.")

# =========================================================
# SUPPORT / RESISTANCE
# =========================================================
def set_support_resistance(new_support, new_resistance):
    global support, resistance, state, last_1h_time, last_15m_time
    if new_support >= new_resistance:
        send_telegram("❌ Support must be below Resistance.")
        return
    support = float(new_support)
    resistance = float(new_resistance)
    reset_trade()
    state = "WAITING_1H"
    current_1h = get_latest_closed("1h")
    current_15m = get_latest_closed("15m")
    last_1h_time = candle_time(current_1h) if current_1h else None
    last_15m_time = candle_time(current_15m) if current_15m else None
    save_state()
    print("NEW SUPPORT / RESISTANCE", support, resistance, state)
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
            params={"offset": last_update_id + 1, "timeout": 1},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            print("Telegram getUpdates ERROR:", data)
            return
        for update in data.get("result", []):
            last_update_id = update["update_id"]
            save_state()
            message = update.get("message")
            if not message:
                continue
            text = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))
            if chat_id != str(CHAT_ID):
                continue
            if text.startswith("/support"):
                parts = text.split()
                if len(parts) != 3:
                    send_telegram("❌ Wrong format.\n\nUse:\n/support 4275 4300")
                    continue
                try:
                    new_support = float(parts[1])
                    new_resistance = float(parts[2])
                    if new_support >= new_resistance:
                        send_telegram("❌ Support must be below Resistance.")
                        continue
                    set_support_resistance(new_support, new_resistance)
                except ValueError:
                    send_telegram("❌ Invalid prices.\nUse: /support 4275 4300")
            elif text == "/cancel":
                reset_everything()
                send_telegram(
                    "❌ SUPPORT / RESISTANCE CANCELLED\n\n"
                    "⏳ Waiting for new Support / Resistance.\n\n"
                    "Use:\n/support 4275 4300"
                )
            elif text == "/status":
                if support is None or resistance is None:
                    send_telegram("⚠️ Support / Resistance not set.\n\nUse: /support 4275 4300")
                else:
                    send_telegram(
                        "📊 XAUUSD BOT STATUS\n\n"
                        f"🟦 Support: {support:.2f}\n"
                        f"🟥 Resistance: {resistance:.2f}\n"
                        f"🤖 State: {state}\n"
                        "🔒 Signal-only: NO ORDERS"
                    )
    except Exception as exc:
        print("Telegram CHECK ERROR:", repr(exc))

# =========================================================
# 1H BREAKOUT
# =========================================================
def check_1h():
    global state, last_1h_time, last_15m_time
    if support is None or resistance is None:
        return
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
        last_15m_time = candle_time(current) if current else None
        save_state()
        send_telegram(
            "🟢 CONFIRMATION FOR TODAY: BUY\n\n"
            "1H breakout confirmed. Wait for Hammer or Bullish Engulfing on a CLOSED 15M candle."
        )
    elif close_price <= support - BREAKOUT_DISTANCE:
        state = "WAITING_SELL_15M"
        current = get_latest_closed("15m")
        last_15m_time = candle_time(current) if current else None
        save_state()
        send_telegram(
            "🔴 CONFIRMATION FOR TODAY: SELL\n\n"
            "1H breakout confirmed. Wait for Shooting Star or Bearish Engulfing on a CLOSED 15M candle."
        )
    else:
        print("NO 1H BREAKOUT")
        save_state()

# =========================================================
# 15M SIGNAL CONFIRMATION
# =========================================================
def check_buy_confirmation():
    global state, last_15m_time, direction, entry, stop_loss, tp1, tp2
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
        save_state()
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
    save_state()
    pattern = "Hammer" if hammer else "Bullish Engulfing"
    send_telegram(
        "🟢 XAUUSD BUY SIGNAL\n\n"
        f"Pattern: {pattern}\nEntry: {entry:.2f}\nStop Loss: {stop_loss:.2f}\n"
        f"TP1: {tp1:.2f}\nTP2: {tp2:.2f}\n\nSIGNAL ONLY — NO ORDER PLACED"
    )


def check_sell_confirmation():
    global state, last_15m_time, direction, entry, stop_loss, tp1, tp2
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
        save_state()
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
    save_state()
    pattern = "Shooting Star" if shooting else "Bearish Engulfing"
    send_telegram(
        "🔴 XAUUSD SELL SIGNAL\n\n"
        f"Pattern: {pattern}\nEntry: {entry:.2f}\nStop Loss: {stop_loss:.2f}\n"
        f"TP1: {tp1:.2f}\nTP2: {tp2:.2f}\n\nSIGNAL ONLY — NO ORDER PLACED"
    )

# =========================================================
# TRADE MONITORING
# =========================================================
def manage_buy():
    global tp1_hit, stop_loss, tp1_candle_time
    candle = get_latest_monitoring_candle()
    if candle is None:
        return
    candle_id = candle_time(candle)
    try:
        _, high, low, close = candle_prices(candle)
    except (KeyError, TypeError, ValueError) as exc:
        print("BUY MONITOR DATA ERROR:", repr(exc))
        return
    print(f"BUY MONITOR | Price: {close:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f} | SL: {stop_loss:.2f}")
    if not tp1_hit:
        # Conservative rule: if SL and TP1 are both touched within the same poll/candle, count SL first.
        if low <= stop_loss:
            finish_trade_sl()
            send_telegram(
                "⛔ BUY SIGNAL: STOP LOSS REACHED\n\n"
                f"Entry: {entry:.2f}\nStop Loss: {stop_loss:.2f}\n\n"
                "The signal is finished.\nWaiting for new Support / Resistance."
            )
            reset_everything()
            return
        if high >= tp1:
            tp1_hit = True
            stop_loss = entry
            tp1_candle_time = candle_id
            save_state()
            send_telegram(
                "🎉 BUY TP1 REACHED\n\nTarget One — 100 PIPS\nStop Loss moved to Breakeven.\n\n"
                f"Entry: {entry:.2f}\nNew Stop Loss: {entry:.2f}\nTP2: {tp2:.2f}\n\n"
                "Monitoring continues every 15 seconds."
            )
            return
    else:
        if high >= tp2:
            finish_trade_tp2()
            send_telegram(
                "🏆 BUY TP2 REACHED\n\nTarget Two — 150 PIPS\n"
                f"Entry: {entry:.2f}\nTP2: {tp2:.2f}\n\n"
                "Signal monitoring finished.\nWaiting for new Support / Resistance."
            )
            reset_everything()
            return
        if candle_id == tp1_candle_time:
            return
        if low <= entry:
            finish_trade_breakeven()
            send_telegram(
                "⚠️ BUY BREAKEVEN REACHED\n\nTarget One: 100 PIPS was reached first.\n"
                "Price returned to Entry.\n\nWaiting for new Support / Resistance."
            )
            reset_everything()
            return


def manage_sell():
    global tp1_hit, stop_loss, tp1_candle_time
    candle = get_latest_monitoring_candle()
    if candle is None:
        return
    candle_id = candle_time(candle)
    try:
        _, high, low, close = candle_prices(candle)
    except (KeyError, TypeError, ValueError) as exc:
        print("SELL MONITOR DATA ERROR:", repr(exc))
        return
    print(f"SELL MONITOR | Price: {close:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f} | SL: {stop_loss:.2f}")
    if not tp1_hit:
        if high >= stop_loss:
            finish_trade_sl()
            send_telegram(
                "⛔ SELL SIGNAL: STOP LOSS REACHED\n\n"
                f"Entry: {entry:.2f}\nStop Loss: {stop_loss:.2f}\n\n"
                "The signal is finished.\nWaiting for new Support / Resistance."
            )
            reset_everything()
            return
        if low <= tp1:
            tp1_hit = True
            stop_loss = entry
            tp1_candle_time = candle_id
            save_state()
            send_telegram(
                "🎉 SELL TP1 REACHED\n\nTarget One — 100 PIPS\nStop Loss moved to Breakeven.\n\n"
                f"Entry: {entry:.2f}\nNew Stop Loss: {entry:.2f}\nTP2: {tp2:.2f}\n\n"
                "Monitoring continues every 15 seconds."
            )
            return
    else:
        if low <= tp2:
            finish_trade_tp2()
            send_telegram(
                "🏆 SELL TP2 REACHED\n\nTarget Two — 150 PIPS\n"
                f"Entry: {entry:.2f}\nTP2: {tp2:.2f}\n\n"
                "Signal monitoring finished.\nWaiting for new Support / Resistance."
            )
            reset_everything()
            return
        if candle_id == tp1_candle_time:
            return
        if high >= entry:
            finish_trade_breakeven()
            send_telegram(
                "⚠️ SELL BREAKEVEN REACHED\n\nTarget One: 100 PIPS was reached first.\n"
                "Price returned to Entry.\n\nWaiting for new Support / Resistance."
            )
            reset_everything()
            return

# =========================================================
# WEEKLY REPORT
# =========================================================
def make_weekly_report():
    total_profit = 0.0
    total_loss = 0.0
    total_signals = 0
    wins = 0
    losses = 0
    for day in DAYS:
        for trade in weekly_results.get(day, []):
            total_signals += 1
            total_profit += float(trade.get("profit", 0) or 0)
            total_loss += float(trade.get("stop_loss", 0) or 0)
            status = trade.get("status", "")
            if status == "SL":
                losses += 1
            else:
                wins += 1
    total_profit = round(total_profit, 1)
    total_loss = round(total_loss, 1)
    net = round(total_profit - total_loss, 1)
    net_text = f"+{net:g}" if net >= 0 else f"−{abs(net):g}"
    profit_text = f"+{total_profit:g}"
    loss_text = f"−{total_loss:g}"
    return (
        "📊 ئەنجامی هەفتانەی XAUUSD\n\n"
        "ئەنجامی کۆتایی بە پیپ\n"
        f"{net_text} Pips\n\n"
        f"🟢 کۆی قازانج: {profit_text} Pips\n"
        f"🔴 کۆی زیان لە ستۆپ: {loss_text} Pips\n\n"
        f"📈 ژمارەی سیگناڵ: {total_signals}\n"
        f"✅ سیگناڵی بردنەوە: {wins}\n"
        f"❌ سیگناڵی ستۆپ: {losses}\n\n"
        "تێبینی: ئەنجامەکان بەپێی چاودێری سیگناڵەکانن، نەک تۆماری بروکەر."
    )


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
        weekly_results = {day: [] for day in DAYS}
        save_state()
        print("WEEKLY REPORT SENT")

# =========================================================
# STARTUP
# =========================================================
load_state()
print("================================")
print("XAUUSD TELEGRAM SIGNAL BOT")
print("Signal-only mode: NO ORDERS")
print("Time zone: Asia/Baghdad")
print("Target monitoring: every 15 seconds")
print("Saved state file:", DATA_FILE)
print("================================")
if not BOT_TOKEN or not CHAT_ID:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Railway Variables.")

# =========================================================
# MAIN LOOP
# =========================================================
while True:
    try:
        check_telegram()
        check_weekly_report()
        # Do not create/manage market signals during weekends.
        if iraq_now().weekday() >= 5:
            time.sleep(POLL_SECONDS)
            continue
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
        save_state()
        time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("Bot stopped.")
        save_state()
        break
    except Exception as exc:
        print("MAIN ERROR:", repr(exc))
        save_state()
        time.sleep(POLL_SECONDS)
