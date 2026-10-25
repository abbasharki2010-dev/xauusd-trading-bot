import os
import json
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# ==========================================
# XAUUSD TELEGRAM SIGNAL BOT
# SIGNAL ONLY - NO ORDERS
# Timezone: Asia/Baghdad
# ==========================================

BOT_NAME = "XAUUSD TELEGRAM SIGNAL BOT"
DATA_URL = "https://biquote.io/api/XAUUSD/ohlc"
TZ = ZoneInfo("Asia/Baghdad")

POLL_SECONDS = 15
BREAKOUT_DISTANCE = 5.0
STOP_BUFFER = 3.0

# $1.00 price movement = 10 pips
TARGET_PIPS = [50, 70, 80, 100, 150]
TARGET_DISTANCES = [p / 10.0 for p in TARGET_PIPS]
TARGET_NAMES = ["One", "Two", "Three", "Four", "Five"]

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DATA_FILE = os.getenv("BOT_DATA_FILE", "bot_state.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("xauusd-bot")
session = requests.Session()

DEFAULT_STATE = {
    "support": None,
    "resistance": None,
    "state": "WAITING_SUPPORT_RESISTANCE",
    "direction": None,
    "last_1h_time": None,
    "last_15m_time": None,
    "current_trade": None,
    "weekly_results": [],
    "last_report_week": None,
    "telegram_update_offset": 0,
}


def now():
    return datetime.now(TZ)


def save_state():
    try:
        with open(DATA_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(DATA_FILE + ".tmp", DATA_FILE)
    except Exception:
        log.exception("Could not save bot state")


def load_state():
    result = DEFAULT_STATE.copy()

    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                result.update(json.load(f))
        except Exception:
            log.exception("Could not load bot state")

    if not isinstance(result.get("weekly_results"), list):
        result["weekly_results"] = []

    # Avoid applying the new targets to an old-format active trade.
    trade = result.get("current_trade")
    if trade and "targets" not in trade:
        result["current_trade"] = None
        result["direction"] = None
        result["state"] = (
            "WAITING_1H"
            if result.get("support") is not None
            and result.get("resistance") is not None
            else "WAITING_SUPPORT_RESISTANCE"
        )

    return result


state = load_state()


def fmt(value):
    return f"{float(value):.2f}"


def to_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        log.error("Telegram token or chat ID is missing.")
        return False

    try:
        response = session.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": message,
            },
            timeout=20,
        )
        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            log.error("Telegram API error: %s", result)
            return False

        return True

    except Exception:
        log.exception("Could not send Telegram message")
        return False


# ==========================================
# MARKET DATA
# ==========================================

def normalize_candle(item):
    if not isinstance(item, dict):
        return None

    def get_value(*keys):
        for key in keys:
            if item.get(key) is not None:
                return item[key]
        return None

    o = to_number(get_value("open", "o", "Open"))
    h = to_number(get_value("high", "h", "High"))
    low = to_number(get_value("low", "l", "Low"))
    c = to_number(get_value("close", "c", "Close", "price"))
    candle_time = get_value(
        "time", "timestamp", "datetime", "date",
        "t", "open_time", "openTime"
    )

    if None in (o, h, low, c):
        return None

    if isinstance(candle_time, (int, float)):
        try:
            if candle_time > 10_000_000_000:
                candle_time = candle_time / 1000
            candle_time = datetime.utcfromtimestamp(
                candle_time
            ).isoformat()
        except Exception:
            candle_time = str(candle_time)

    return {
        "time": str(candle_time) if candle_time is not None else None,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
    }


def extract_candles(payload):
    items = []

    if isinstance(payload, list):
        items = payload

    elif isinstance(payload, dict):
        for key in ("data", "candles", "ohlc", "result", "prices", "items"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break

    candles = []

    for item in items:
        candle = normalize_candle(item)
        if candle:
            candles.append(candle)

    return candles


def fetch_candles(timeframe):
    # The API must support the timeframe requested for correct signals.
    parameter_options = [
        {"timeframe": timeframe},
        {"interval": timeframe},
        {"period": timeframe},
        {"tf": timeframe},
        {},
    ]

    for params in parameter_options:
        try:
            response = session.get(
                DATA_URL,
                params=params,
                timeout=20,
            )
            response.raise_for_status()
            candles = extract_candles(response.json())

            if candles:
                return candles

        except Exception:
            log.exception("Market data request failed for %s", timeframe)

    return []


def candle_id(candle):
    return str(candle.get("time") or "")


def get_closed_candles(timeframe):
    candles = fetch_candles(timeframe)

    if len(candles) < 2:
        return []

    # Assumes the API returns candles oldest-first
    # and the newest candle is still forming.
    return candles[:-1]


def get_latest_monitoring_candle():
    candles = fetch_candles("15m")
    return candles[-1] if candles else None


# ==========================================
# TELEGRAM COMMANDS
# ==========================================

def reset_everything():
    state["support"] = None
    state["resistance"] = None
    state["state"] = "WAITING_SUPPORT_RESISTANCE"
    state["direction"] = None
    state["last_1h_time"] = None
    state["last_15m_time"] = None
    state["current_trade"] = None
    save_state()


def handle_command(text):
    parts = text.split()
    command = parts[0].split("@")[0].lower()

    if command == "/start":
        send_telegram(
            "XAUUSD TELEGRAM SIGNAL BOT\n"
            "Signal-only mode: NO ORDERS\n"
            "Time zone: Asia/Baghdad\n\n"
            "Commands:\n"
            "/support 4275 4300\n"
            "/status\n"
            "/cancel"
        )
        return

    if command == "/support":
        if len(parts) != 3:
            send_telegram(
                "Usage: /support SUPPORT RESISTANCE\n"
                "Example: /support 4275 4300"
            )
            return

        support = to_number(parts[1])
        resistance = to_number(parts[2])

        if (
            support is None
            or resistance is None
            or support >= resistance
        ):
            send_telegram("Support must be below Resistance.")
            return

        state["support"] = support
        state["resistance"] = resistance
        state["state"] = "WAITING_1H"
        state["direction"] = None
        state["current_trade"] = None
        state["last_1h_time"] = None
        state["last_15m_time"] = None
        save_state()

        send_telegram(
            "✅ SUPPORT / RESISTANCE UPDATED\n"
            f"Support: {fmt(support)}\n"
            f"Resistance: {fmt(resistance)}\n"
            "Bot is watching XAUUSD.\n"
            "Waiting for a CLOSED 1H breakout candle."
        )
        return

    if command == "/status":
        message = (
            "XAUUSD BOT STATUS\n"
            "Mode: SIGNAL ONLY — NO ORDERS\n"
            f"State: {state['state']}\n"
            f"Support: {fmt(state['support']) if state['support'] is not None else 'Not set'}\n"
            f"Resistance: {fmt(state['resistance']) if state['resistance'] is not None else 'Not set'}"
        )

        trade = state.get("current_trade")

        if trade:
            message += (
                f"\nDirection: {trade['direction']}"
                f"\nEntry: {fmt(trade['entry'])}"
                f"\nStop Loss: {fmt(trade['stop_loss'])}"
                f"\nHighest target: {trade['highest_target_index']}"
            )

        send_telegram(message)
        return

    if command == "/cancel":
        reset_everything()
        send_telegram(
            "🛑 Cancelled.\n"
            "Support/Resistance cleared.\n"
            "Waiting for new Support/Resistance."
        )
        return

    send_telegram("Unknown command. Use /support, /status, or /cancel.")


def poll_telegram_commands():
    if not TOKEN:
        return

    offset = int(state.get("telegram_update_offset", 0))

    try:
        response = session.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"timeout": 0, "offset": offset},
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()

        for update in result.get("result", []):
            state["telegram_update_offset"] = max(
                int(state.get("telegram_update_offset", 0)),
                int(update["update_id"]) + 1,
            )

            message = update.get("message", {})
            chat_id = str(message.get("chat", {}).get("id", ""))

            if CHAT_ID and chat_id != str(CHAT_ID):
                continue

            text = (message.get("text") or "").strip()

            if text.startswith("/"):
                handle_command(text)

        save_state()

    except Exception:
        log.exception("Telegram command polling failed")


# ==========================================
# 1H BREAKOUT CONFIRMATION
# ==========================================

def process_1h_breakout():
    if state["state"] != "WAITING_1H":
        return

    if state["support"] is None or state["resistance"] is None:
        return

    candles = get_closed_candles("1h")

    if not candles:
        return

    candle = candles[-1]
    cid = candle_id(candle)

    if not cid or cid == state.get("last_1h_time"):
        return

    state["last_1h_time"] = cid
    close = candle["close"]

    if close >= state["resistance"] + BREAKOUT_DISTANCE:
        state["direction"] = "BUY"
        state["state"] = "WAITING_15M_PATTERN_BUY"
        save_state()

        send_telegram(
            "🟢 CONFIRMATION FOR TODAY: BUY\n"
            "1H breakout confirmed.\n"
            "Wait for Hammer or Bullish Engulfing "
            "on a CLOSED 15M candle."
        )

    elif close <= state["support"] - BREAKOUT_DISTANCE:
        state["direction"] = "SELL"
        state["state"] = "WAITING_15M_PATTERN_SELL"
        save_state()

        send_telegram(
            "🔴 CONFIRMATION FOR TODAY: SELL\n"
            "1H breakout confirmed.\n"
            "Wait for Shooting Star or Bearish Engulfing "
            "on a CLOSED 15M candle."
        )

    save_state()


# ==========================================
# 15M CANDLE PATTERNS
# ==========================================

def is_hammer(candle):
    body = abs(candle["close"] - candle["open"])

    if body <= 0:
        return False

    lower_wick = min(candle["open"], candle["close"]) - candle["low"]
    upper_wick = candle["high"] - max(candle["open"], candle["close"])

    return lower_wick >= 2 * body and upper_wick <= body


def is_shooting_star(candle):
    body = abs(candle["close"] - candle["open"])

    if body <= 0:
        return False

    lower_wick = min(candle["open"], candle["close"]) - candle["low"]
    upper_wick = candle["high"] - max(candle["open"], candle["close"])

    return upper_wick >= 2 * body and lower_wick <= body


def bullish_engulfing(previous, current):
    return (
        previous["close"] < previous["open"]
        and current["close"] > current["open"]
        and current["open"] <= previous["close"]
        and current["close"] >= previous["open"]
    )


def bearish_engulfing(previous, current):
    return (
        previous["close"] > previous["open"]
        and current["close"] < current["open"]
        and current["open"] >= previous["close"]
        and current["close"] <= previous["open"]
    )


def process_15m_pattern():
    if state["state"] not in (
        "WAITING_15M_PATTERN_BUY",
        "WAITING_15M_PATTERN_SELL",
    ):
        return

    candles = get_closed_candles("15m")

    if len(candles) < 2:
        return

    previous, current = candles[-2], candles[-1]
    cid = candle_id(current)

    if not cid or cid == state.get("last_15m_time"):
        return

    state["last_15m_time"] = cid
    direction = state["direction"]
    pattern = None

    if direction == "BUY":
        if is_hammer(current):
            pattern = "Hammer"
        elif bullish_engulfing(previous, current):
            pattern = "Bullish Engulfing"

    elif direction == "SELL":
        if is_shooting_star(current):
            pattern = "Shooting Star"
        elif bearish_engulfing(previous, current):
            pattern = "Bearish Engulfing"

    if not pattern:
        save_state()
        return

    entry = current["close"]

    if direction == "BUY":
        stop_loss = current["low"] - STOP_BUFFER
        targets = [entry + distance for distance in TARGET_DISTANCES]
    else:
        stop_loss = current["high"] + STOP_BUFFER
        targets = [entry - distance for distance in TARGET_DISTANCES]

    state["current_trade"] = {
        "direction": direction,
        "pattern": pattern,
        "entry": entry,
        "initial_stop_loss": stop_loss,
        "stop_loss": stop_loss,
        "targets": targets,
        "target_pips": TARGET_PIPS[:],
        "target_hits": [False] * 5,
        "highest_target_index": 0,
        "entry_time": cid,
    }

    state["state"] = "MANAGING_BUY" if direction == "BUY" else "MANAGING_SELL"
    save_state()

    targets_text = "\n".join(
        f"TP{i}: {fmt(target)} ({TARGET_PIPS[i - 1]} Pips)"
        for i, target in enumerate(targets, start=1)
    )

    send_telegram(
        f"{'🟢' if direction == 'BUY' else '🔴'} XAUUSD {direction} SIGNAL\n\n"
        f"Pattern: {pattern}\n"
        f"Entry: {fmt(entry)}\n"
        f"Stop Loss: {fmt(stop_loss)}\n"
        f"{targets_text}\n\n"
        "SIGNAL ONLY — NO ORDER PLACED."
    )


# ==========================================
# TRADE RESULT TRACKING
# ==========================================

def record_result(trade, status, pips):
    state.setdefault("weekly_results", []).append({
        "date": now().date().isoformat(),
        "direction": trade["direction"],
        "status": status,
        "pips": int(pips),
        "entry": trade["entry"],
        "pattern": trade["pattern"],
    })
    save_state()


def finish_after_stop():
    trade = state.get("current_trade")

    if not trade:
        return

    highest = int(trade.get("highest_target_index", 0))

    if highest > 0:
        # After TP1, TP2 or TP3, return to original SL
        # records the highest target's profit, not a loss.
        pips = int(trade["target_pips"][highest - 1])

        record_result(trade, f"TP{highest}_THEN_STOP", pips)

        send_telegram(
            f"↩️ Price returned to stop after Target {TARGET_NAMES[highest - 1]}.\n"
            f"Recorded result: +{pips} Pips.\n"
            "This is NOT counted as an SL loss."
        )

    else:
        loss_pips = -int(round(
            abs(trade["entry"] - trade["initial_stop_loss"]) * 10
        ))

        record_result(trade, "SL", loss_pips)

        send_telegram(
            "❌ XAUUSD STOP LOSS HIT\n"
            f"Entry: {fmt(trade['entry'])}\n"
            f"Stop Loss: {fmt(trade['initial_stop_loss'])}\n"
            f"Result: {loss_pips} Pips\n"
            "No target was reached."
        )

    reset_everything()


def finish_at_target_five():
    trade = state.get("current_trade")

    if not trade:
        return

    record_result(trade, "TP5", 150)

    send_telegram(
        "🏁 TARGET FIVE REACHED\n"
        "Congratulations Target Five 150 Pips\n"
        "Signal tracking finished. No order was placed."
    )

    reset_everything()


def stop_was_hit(trade, candle):
    stop = float(trade["stop_loss"])

    if trade["direction"] == "BUY":
        return candle["low"] <= stop

    return candle["high"] >= stop


def target_was_hit(trade, target, candle):
    if trade["direction"] == "BUY":
        return candle["high"] >= target

    return candle["low"] <= target


def process_trade_management():
    trade = state.get("current_trade")

    if not trade:
        return

    candle = get_latest_monitoring_candle()

    if not candle:
        return

    # Do not manage the candle that created the signal.
    if candle_id(candle) == trade.get("entry_time"):
        return

    # IMPORTANT:
    # If the stop and a target are both inside the same candle,
    # candle OHLC cannot tell us which was reached first.
    # The bot checks the stop first as a conservative rule.
    if stop_was_hit(trade, candle):
        finish_after_stop()
        return

    for index, target in enumerate(trade["targets"], start=1):
        if trade["target_hits"][index - 1]:
            continue

        if not target_was_hit(trade, target, candle):
            continue

        trade["target_hits"][index - 1] = True
        trade["highest_target_index"] = max(
            trade["highest_target_index"],
            index
        )
        save_state()

        pips = int(trade["target_pips"][index - 1])

        send_telegram(
            f"🎉 Congratulations Target {TARGET_NAMES[index - 1]} {pips} Pips"
        )

        if index == 4:
            # Move stop to entry only after Target Four.
            trade["stop_loss"] = trade["entry"]
            save_state()

            send_telegram(
                "🔒 Target Four reached (100 Pips).\n"
                f"Stop Loss moved to Breakeven: {fmt(trade['entry'])}."
            )

        if index == 5:
            finish_at_target_five()
            return

    save_state()


# ==========================================
# WEEKLY REPORT
# ==========================================

def week_key(date_value):
    iso = date_value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def send_weekly_report_if_due():
    current = now()

    # Friday, 23:00 or later in Baghdad time.
    if current.weekday() != 4 or current.hour < 23:
        return

    key = week_key(current.date())

    if state.get("last_report_week") == key:
        return

    results = [
        item for item in state.get("weekly_results", [])
        if item.get("date")
        and week_key(datetime.fromisoformat(item["date"]).date()) == key
    ]

    wins = sum(1 for item in results if item["status"] != "SL")
    losses = sum(1 for item in results if item["status"] == "SL")
    net_pips = sum(int(item["pips"]) for item in results)

    lines = [
        "📊 XAUUSD WEEKLY RESULTS",
        f"Week: {key}",
        "Signal tracking only — not broker records.",
        f"Trades: {len(results)}",
        f"Wins: {wins}",
        f"Losses: {losses}",
        f"Net Pips: {net_pips:+d}",
    ]

    for item in results:
        lines.append(
            f"{item['direction']} | {item['status']} | "
            f"{int(item['pips']):+d} pips | {item['date']}"
        )

    if not results:
        lines.append("No completed trades this week.")

    send_telegram("\n".join(lines))

    state["last_report_week"] = key
    save_state()


# ==========================================
# MAIN LOOP
# ==========================================

def main():
    if not TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID. "
            "Set both in Railway Variables."
        )

    log.info("%s started — SIGNAL ONLY.", BOT_NAME)

    send_telegram(
        "✅ XAUUSD TELEGRAM SIGNAL BOT STARTED\n"
        "Signal-only mode: NO ORDERS\n"
        "Time zone: Asia/Baghdad\n"
        "Use /support SUPPORT RESISTANCE to begin."
    )

    while True:
        try:
            poll_telegram_commands()

            # No new trading signals or trade management on weekends.
            if now().weekday() < 5:
                if state["state"] == "WAITING_1H":
                    process_1h_breakout()

                if state["state"] in (
                    "WAITING_15M_PATTERN_BUY",
                    "WAITING_15M_PATTERN_SELL",
                ):
                    process_15m_pattern()

                if state["state"] in ("MANAGING_BUY", "MANAGING_SELL"):
                    process_trade_management()

            send_weekly_report_if_due()
            save_state()

        except Exception:
            log.exception("Main loop error")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
