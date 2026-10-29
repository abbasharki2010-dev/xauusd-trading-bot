
import os
import json
import time
import logging
import requests

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# XAUUSD TELEGRAM SIGNAL BOT
# SIGNAL ONLY — NO AUTOMATIC ORDERS

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

DATA_URL = "https://biquote.io/api/XAUUSD/ohlc"
TZ = ZoneInfo("Asia/Baghdad")
UTC = ZoneInfo("UTC")

POLL_SECONDS = 15
STEP = 10.0
TP_DISTANCE = 10.0
PIPS_PER_PRICE_UNIT = 10
MAX_PROFITS_PER_DAY = 3

STATE_FILE = os.getenv("BOT_DATA_FILE", "scalping_state.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

session = requests.Session()
session.headers.update({"User-Agent": "XAUUSDManualBiasBot/4.0"})

state = {
    "bias": None,
    "reference": None,
    "levels": [],
    "main_trade": None,
    "last_5m": None,
    "offset": 0,
    "daily_closed": False,
    "trade_history": [],
    "last_weekly_report": None,
    "plan_date": None,
}


# ---------------- BASIC FUNCTIONS ----------------

def now():
    return datetime.now(TZ)


def now_text():
    return now().strftime("%Y-%m-%d %H:%M:%S")


def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error("Save state failed: %s", e)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            state.update(saved)
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.error("Load state failed: %s", e)


def send_message(message):
    if not TOKEN or not CHAT_ID:
        logging.error("Telegram token or chat ID missing")
        return False

    try:
        response = session.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": message
            },
            timeout=15
        )
        response.raise_for_status()
        result = response.json()

        if not result.get("ok"):
            logging.error("Telegram response: %s", result)
            return False

        return True

    except Exception as e:
        logging.error("Telegram send failed: %s", e)
        return False


def get_updates():
    try:
        response = session.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={
                "offset": state.get("offset", 0),
                "timeout": 1
            },
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        return data.get("result", []) if data.get("ok") else []

    except Exception as e:
        logging.error("Telegram polling failed: %s", e)
        return []


# ---------------- MARKET DATA ----------------

def find_candles(data):
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return data
        return None

    if isinstance(data, dict):
        for key in (
            "bars", "candles", "data",
            "result", "ohlc", "prices"
        ):
            if key in data:
                found = find_candles(data[key])
                if found is not None:
                    return found

    return None


def normalize(raw):
    if not isinstance(raw, dict):
        return None

    stamp = next(
        (
            raw[k] for k in (
                "openTime", "time", "timestamp",
                "datetime", "date", "open_time",
                "time_open", "t"
            )
            if raw.get(k) is not None
        ),
        None
    )

    op = raw.get("open", raw.get("Open", raw.get("o")))
    hi = raw.get("high", raw.get("High", raw.get("h")))
    lo = raw.get("low", raw.get("Low", raw.get("l")))
    cl = raw.get("close", raw.get("Close", raw.get("c")))

    if any(v is None for v in (stamp, op, hi, lo, cl)):
        return None

    try:
        if isinstance(stamp, (int, float)):
            stamp = float(stamp)
            if stamp > 10_000_000_000:
                stamp /= 1000

            candle_id = datetime.fromtimestamp(
                stamp, tz=UTC
            ).isoformat()

        else:
            text_stamp = str(stamp).strip()

            if text_stamp.endswith("Z"):
                text_stamp = text_stamp[:-1] + "+00:00"

            dt = datetime.fromisoformat(text_stamp)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)

            candle_id = dt.astimezone(UTC).isoformat()

        is_open = raw.get("isOpen", raw.get("is_open"))

        if isinstance(is_open, str):
            if is_open.lower().strip() == "true":
                is_open = True
            elif is_open.lower().strip() == "false":
                is_open = False

        # Do not guess whether a candle is closed.
        if is_open not in (True, False):
            return None

        candle = {
            "id": candle_id,
            "open": float(op),
            "high": float(hi),
            "low": float(lo),
            "close": float(cl),
            "is_open": is_open
        }

        if (
            candle["high"] < max(candle["open"], candle["close"])
            or candle["low"] > min(candle["open"], candle["close"])
            or candle["high"] < candle["low"]
        ):
            return None

        return candle

    except (ValueError, TypeError, OverflowError):
        return None


def fetch_closed_5m():
    try:
        response = session.get(
            DATA_URL,
            params={"interval": "5m", "limit": 100},
            timeout=15
        )
        response.raise_for_status()

        raw_candles = find_candles(response.json())

        if not raw_candles:
            logging.error("No candles returned")
            return []

        parsed = [
            normalize(candle)
            for candle in raw_candles
        ]
        parsed = [c for c in parsed if c is not None]

        if not parsed:
            return []

        closed = [
            candle for candle in parsed
            if candle["is_open"] is False
        ]

        unique = {c["id"]: c for c in closed}

        return sorted(
            unique.values(),
            key=lambda candle: candle["id"]
        )

    except Exception as e:
        logging.error("OHLC fetch failed: %s", e)
        return []


# ---------------- TRADES AND DAILY PLAN ----------------

def make_trade(name, entry, bias, activated_candle=None):
    target = (
        entry + TP_DISTANCE
        if bias == "BUY"
        else entry - TP_DISTANCE
    )

    return {
        "name": name,
        "price": round(entry, 2),
        "target": round(target, 2),
        "status": "ACTIVE" if activated_candle else "WAITING",
        "activated_candle": activated_candle
    }


def clear_plan():
    state["bias"] = None
    state["reference"] = None
    state["levels"] = []
    state["main_trade"] = None
    state["last_5m"] = None
    state["daily_closed"] = False
    state["plan_date"] = None


def set_bias(bias, reference):
    candles = fetch_closed_5m()

    if not candles:
        send_message(
            "⚠️ ناتوانم کاندڵی داخراوی 5M وەربگرم.\n"
            "تکایە کەمێک چاوەڕێ بکە و دووبارە هەوڵ بدە."
        )
        return

    latest_id = candles[-1]["id"]

    state["bias"] = bias
    state["reference"] = round(reference, 2)
    state["last_5m"] = latest_id
    state["daily_closed"] = False
    state["plan_date"] = now().date().isoformat()

    # The main trade starts at the reference price.
    state["main_trade"] = make_trade(
        "مامەڵەی سەرەکی",
        reference,
        bias,
        latest_id
    )

    # Four extra levels: reference +/- 10 and +/- 20.
    prices = sorted([
        reference - 2 * STEP,
        reference - STEP,
        reference + STEP,
        reference + 2 * STEP
    ])

    state["levels"] = [
        make_trade(
            f"ئاست {i}",
            price,
            bias
        )
        for i, price in enumerate(prices, 1)
    ]

    save_state()

    levels_text = "\n".join(
        f"{i}. {item['price']:.2f} → TP {item['target']:.2f}"
        for i, item in enumerate(state["levels"], 1)
    )

    direction = "کڕین 🟢" if bias == "BUY" else "فرۆشتن 🔴"

    send_message(
        "🦅🏆 XAUUSD | پلانی نوێ\n\n"
        f"📊 ئاراستە: {direction}\n"
        f"🎯 نرخی بایەس: {reference:.2f}\n\n"
        "⭐ مامەڵەی سەرەکی:\n"
        f"Entry: {reference:.2f}\n"
        f"TP: {state['main_trade']['target']:.2f}\n\n"
        "📍 چوار ئاستی زیادە:\n"
        f"{levels_text}\n\n"
        "💰 هەر TP = 100 پیپ\n"
        "🏆 کاتێک 3 مامەڵە TP بکەن، "
        "پلانی ئەم ڕۆژە کۆتایی دێت.\n"
        "SIGNAL ONLY — NO ORDERS"
    )


def record_profit(trade):
    pips = int(round(
        abs(trade["target"] - trade["price"])
        * PIPS_PER_PRICE_UNIT
    ))

    state.setdefault("trade_history", []).append({
        "date": now().date().isoformat(),
        "time": now_text(),
        "bias": state.get("bias"),
        "name": trade["name"],
        "entry": trade["price"],
        "target": trade["target"],
        "pips": pips
    })


def close_day():
    state["daily_closed"] = True

    # Cancel the remaining levels so they cannot be counted.
    for item in state.get("levels", []):
        if item["status"] in ("WAITING", "ACTIVE"):
            item["status"] = "CANCELLED"

    main_trade = state.get("main_trade")
    if main_trade and main_trade["status"] == "ACTIVE":
        main_trade["status"] = "CANCELLED"

    save_state()

    profits_today = sum(
        1 for item in state.get("trade_history", [])
        if item.get("date") == now().date().isoformat()
    )

    send_message(
        "🏆🎉 پیرۆز بێت!\n\n"
        "3 مامەڵە بە قازانج کۆتاییان هات.\n"
        "💰 قازانجی ئەم پلانه: 300 پیپ\n"
        "🛑 دوو ئاستی ماوە هەڵوەشێنرانەوە.\n"
        "⏸️ بۆ ئەم پلانه سیگناڵی نوێ نانێردرێت.\n\n"
        "بۆ ڕۆژی دواتر بایەسی نوێ دابنێ:\n"
        "/bias BUY 4030\n"
        "یان\n"
        "/bias SELL 4030\n\n"
        "SIGNAL ONLY — NO ORDERS"
    )


def count_plan_profits():
    # Count only profits from the current plan date.
    plan_date = state.get("plan_date")

    return sum(
        1 for item in state.get("trade_history", [])
        if item.get("date") == plan_date
    )


# ---------------- MONITORING ----------------

def target_reached(trade, candle, bias):
    if bias == "BUY":
        return candle["high"] >= trade["target"]

    return candle["low"] <= trade["target"]


def level_touched(trade, candle):
    return (
        candle["low"] <= trade["price"]
        <= candle["high"]
    )


def process_trade(trade, candle):
    if trade["status"] != "ACTIVE":
        return False

    activation = trade.get("activated_candle")

    # Do not count target movement on the same candle
    # where the trade became active.
    if activation and candle["id"] <= activation:
        return False

    if not target_reached(trade, candle, state["bias"]):
        return False

    trade["status"] = "PROFIT_HIT"
    record_profit(trade)

    send_message(
        "🎉🏆 پیرۆز بێت! مامەڵەکە قازانجی کرد.\n\n"
        f"📌 {trade['name']}\n"
        f"🔹 Entry: {trade['price']:.2f}\n"
        f"🎯 TP: {trade['target']:.2f}\n"
        f"💰 قازانج: {int(TP_DISTANCE * PIPS_PER_PRICE_UNIT)} پیپ\n"
        f"📊 ئاراستە: {state['bias']}\n\n"
        "SIGNAL ONLY — NO ORDER PLACED"
    )

    return True


def monitor_levels():
    if state.get("bias") not in ("BUY", "SELL"):
        return

    if state.get("daily_closed"):
        return

    candles = fetch_closed_5m()

    if not candles:
        return

    last_id = state.get("last_5m")

    if not last_id:
        state["last_5m"] = candles[-1]["id"]
        save_state()
        return

    new_candles = [
        candle for candle in candles
        if candle["id"] > last_id
    ]

    if not new_candles:
        return

    for candle in new_candles:
        if state.get("daily_closed"):
            break

        # Check the main trade first.
        main_trade = state.get("main_trade")

        if main_trade:
            process_trade(main_trade, candle)

        if count_plan_profits() >= MAX_PROFITS_PER_DAY:
            close_day()
            break

        # Check targets of activated extra levels.
        for trade in state.get("levels", []):
            if state.get("daily_closed"):
                break

            process_trade(trade, candle)

            if count_plan_profits() >= MAX_PROFITS_PER_DAY:
                close_day()
                break

        if state.get("daily_closed"):
            break

        # Activate any waiting levels touched by this candle.
        for trade in state.get("levels", []):
            if state.get("daily_closed"):
                break

            if trade["status"] != "WAITING":
                continue

            if level_touched(trade, candle):
                trade["status"] = "ACTIVE"
                trade["activated_candle"] = candle["id"]

                send_message(
                    "🔔📍 نرخی ئاستەکە ئەکتیڤ بوو!\n\n"
                    f"📊 ئاراستە: {state['bias']}\n"
                    f"🔹 Entry: {trade['price']:.2f}\n"
                    f"🎯 TP: {trade['target']:.2f}\n"
                    "💰 ئەگەر TP بگات = 100 پیپ\n\n"
                    "SIGNAL ONLY — NO ORDER PLACED\n"
                    f"🕒 {now_text()}"
                )

        save_state()

    state["last_5m"] = new_candles[-1]["id"]
    save_state()


# ---------------- WEEKLY REPORT ----------------

def weekly_start():
    today = now().date()
    return today - timedelta(days=today.weekday())


def weekly_report(manual=False):
    start = weekly_start()
    end = start + timedelta(days=7)

    records = [
        item for item in state.get("trade_history", [])
        if start.isoformat() <= item.get("date", "") < end.isoformat()
    ]

    total_trades = len(records)
    total_pips = sum(item.get("pips", 0) for item in records)

    details = "\n".join(
        f"• {item['date']} | {item['name']} | "
        f"{item['pips']} پیپ"
        for item in records
    )

    if not details:
        details = "هیچ مامەڵەیەکی قازانج‌تەواوبوو تۆمار نەکراوە."

    title = "📊 ڕاپۆرتی هەفتانە" if not manual else "📊 ڕاپۆرتی هەفتانە (داواکراو)"

    send_message(
        f"🦅🏆 {title}\n\n"
        f"📅 لە {start.isoformat()} تا {end.isoformat()}\n"
        f"✅ ژمارەی مامەڵە قازانج‌تەواوبووەکان: {total_trades}\n"
        f"💰 کۆی قازانج: {total_pips} پیپ\n\n"
        f"{details}\n\n"
        "تەنها مامەڵە تەواوبووەکان حساب کراون.\n"
        "SIGNAL ONLY — NO ORDERS"
    )

    state["last_weekly_report"] = start.isoformat()
    save_state()


def check_weekly_report():
    current = now()

    # Friday at or after 23:00 Baghdad time.
    if current.weekday() != 4:
        return

    if current.hour < 23:
        return

    start = weekly_start().isoformat()

    if state.get("last_weekly_report") == start:
        return

    weekly_report(manual=False)


# ---------------- TELEGRAM COMMANDS ----------------

def status_message():
    bias = state.get("bias") or "دانەنراوە"
    reference = state.get("reference")
    main_trade = state.get("main_trade")

    main_text = "نییە"
    if main_trade:
        main_text = (
            f"{main_trade['price']:.2f} | "
            f"TP {main_trade['target']:.2f} | "
            f"{main_trade['status']}"
        )

    levels = state.get("levels", [])
    level_text = "\n".join(
        f"{i}. {item['price']:.2f} → TP {item['target']:.2f}"
        f" | {item['status']}"
        for i, item in enumerate(levels, 1)
    ) or "هیچ ئاستێک نییە."

    wins = count_plan_profits()

    send_message(
        "🟡 XAUUSD | دۆخی بۆت\n\n"
        "Mode: SIGNAL ONLY — NO ORDERS\n"
        f"📊 Bias: {bias}\n"
        f"🎯 Reference: {reference}\n"
        f"⭐ مامەڵەی سەرەکی: {main_text}\n\n"
        f"📍 ئاستەکان:\n{level_text}\n\n"
        f"🏆 ژمارەی قازانجی ئەم پلانە: {wins}/3\n"
        f"🛑 کۆتایی ڕۆژ: {state.get('daily_closed')}\n"
        f"🕒 {now_text()}"
    )


def handle_commands():
    for update in get_updates():
        state["offset"] = update["update_id"] + 1

        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if chat_id != CHAT_ID:
            continue

        if text.startswith("/start"):
            send_message(
                "🦅🏆 XAUUSD SIGNAL BOT\n\n"
                "ئەم بۆتە تەنها ئاگادارکردنەوە دەنێرێت؛ "
                "هیچ ئۆردەرێک ناکات.\n\n"
                "🟢 بایەسی کڕین:\n"
                "/bias BUY 4030\n\n"
                "🔴 بایەسی فرۆشتن:\n"
                "/bias SELL 4045\n\n"
                "📊 دۆخ: /status\n"
                "📅 ڕاپۆرت: /weekly\n"
                "🛑 هەڵوەشاندنەوە: /cancel\n\n"
                "هەر TP = 100 پیپ.\n"
                "٣ قازانج = ٣٠٠ پیپ؛ "
                "پاشان پلانی ڕۆژەکە کۆتایی دێت."
            )

        elif text.startswith("/bias"):
            parts = text.split()

            if (
                len(parts) != 3
                or parts[1].upper() not in ("BUY", "SELL")
            ):
                send_message(
                    "⚠️ شێوازی دروست:\n"
                    "/bias BUY 4030\n"
                    "یان\n"
                    "/bias SELL 4045"
                )
                continue

            try:
                reference = float(parts[2])
                if reference <= 0:
                    raise ValueError
            except ValueError:
                send_message("⚠️ تکایە نرخێکی دروست بنووسە.")
                continue

            set_bias(parts[1].upper(), reference)

        elif text.startswith("/status"):
            status_message()

        elif text.startswith("/weekly"):
            weekly_report(manual=True)

        elif text.startswith("/cancel"):
            clear_plan()
            save_state()

            send_message(
                "🛑 پلانی ئێستا و هەموو ئاستەکان هەڵوەشێنرانەوە.\n\n"
                "بۆ دەستپێکردنەوە:\n"
                "/bias BUY 4030\n"
                "یان\n"
                "/bias SELL 4045"
            )

    save_state()


# ---------------- START BOT ----------------

def main():
    if not TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
            "in Railway Variables."
        )

    load_state()

    send_message(
        "🦅🏆 XAUUSD SIGNAL BOT چالاک بوو.\n\n"
        "SIGNAL ONLY — NO ORDERS\n"
        "بایەس دابنێ:\n"
        "/bias BUY 4030\n"
        "یان\n"
        "/bias SELL 4045\n\n"
        "🕒 Asia/Baghdad"
    )

    logging.info("XAUUSD signal bot started")

    while True:
        try:
            handle_commands()
            monitor_levels()
            check_weekly_report()

        except Exception as e:
            logging.exception("Main loop error: %s", e)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
