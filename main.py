import os
import requests
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import threading
from flask import Flask, request, jsonify
from metaapi_cloud_sdk import MetaApi

# =========================================================
# SETTINGS
# =========================================================

URL = "https://biquote.io/api/XAUUSD/ohlc"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

API_KEY = "eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiIzODg3ZDJjNmQ3NTNmYjRkNThjMjJlOGUzMGMyZmUzYyIsImFjY2Vzc1J1bGVzIjpbeyJpZCI6InRyYWRpbmctYWNjb3VudC1tYW5hZ2VtZW50LWFwaSIsIm1ldGhvZHMiOlsidHJhZGluZy1hY2NvdW50LW1hbmFnZW1lbnQtYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcmVzdC1hcGkiLCJtZXRob2RzIjpbIm1ldGFhcGktYXBpOndzOnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFzdGF0cy1hcGkiLCJtZXRob2RzIjpbIm1ldGFzdGF0cy1hcGk6cmVzdDpub2JsaWM6KiOqIikgZmFsc2UsInRva2VuSWQiOiIyMDIxMDIxMyIsImltcGVyc29uYXRlZCI6ZmFsc2UsInJlYWxVc2VySWQiOiIzODg3ZDJjNmQ3NTNmYjRkNThjMjJlOGUzMGMyZmUzYyIsImlhdCI6MTc5MTczMjg2MywiZXhwIjoxNzk5NTA4ODYzfQ.Pn_ctorbaAXmBlJ6qHCj72Simm6dWPNwFOIOsU4MpGy-4VUYOpEqXr1FI-OJt1pzwjuGqIEn9XmkzIieQ6sB8TTFKIawJWoA6WYqzrWGknsd85pddAeCk0tQN1cViWof5_keCkNqszCbK63XFJHEQ2mH1pdaZg9COCV_VWyBD7xcc3mMzT4Sfm6Pb6Bc952Rry-ksgh1fIteiq27LnFlVtaS_C-b817DPXYTdudaqinwMKPpHHCoUadjbEM8KV69Cyeon27egpQyWLAegk2BucHBQZ4jIuAXX-PdHwneyCX42nBpW_qUk6qB0cgHT6_sjO8Psnj0GQknm3qZplWyGT060g4ciNareE1J8-S0rTSorc6bWwryTjZl2q0sMSCWTT8zMgd1-KFqnrqIbb1RJqXgA0hepfB60LjQGfvEHQ71V0KIjoHeax5HGdHU41pSq3j3Jf2Becqdx7hK0HUH376-836WdkZrSfTRC1-Oo9-fXY0SSTIlkEbAyUuZ7xxhf-e6C6PfZplA3ZFDzBoMvjuMDji-oS-w3hcCnWYcdGAdrvc_qHmfTH9c48z-FGAOMJ2_JuQcRewbycNGmVoNN5jyi5NteNgzM4P6JI3IrHY-KE6CsXKpqaatLha6o55PgjdtHohQw6uq838lXllWg3P37_j-ijno9iU5vxW1Kp4"
ACCOUNT_ID = "3488c28e-d7bf-485d-8d9c-5efb121082b3"

api = None
app = Flask(__name__)

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

weekly_results = {
    "Monday": [], "Tuesday": [], "Wednesday": [], "Thursday": [], "Friday": []
}
current_trade = None
weekly_report_sent = False
last_report_week = None

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
    return body > 0 and lower_wick >= body * 2 and upper_wick <= body

def is_shooting_star(o, h, l, c):
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return body > 0 and upper_wick >= body * 2 and lower_wick <= body

def is_bullish_engulfing(po, pc, co, cc):
    return pc < po and cc > co and co <= pc and cc >= po

def is_bearish_engulfing(po, pc, co, cc):
    return pc > po and cc < co and co >= pc and cc <= po

# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):
    try:
        telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(telegram_url, data={"chat_id": CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print("Telegram ERROR:", e)

# =========================================================
# METAAPI BROKER EXECUTION
# =========================================================

async def execute_trade_on_broker(action, volume=0.01, sl=None, tp=None):
    try:
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        if account.state != 'DEPLOYED':
            await account.deploy()
        await account.wait_connected()

        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        symbol = "XAUUSD"
        if action == "BUY":
            return True, await connection.create_market_buy_order(symbol, volume, sl, tp)
        elif action == "SELL":
            return True, await connection.create_market_sell_order(symbol, volume, sl, tp)
    except Exception as e:
        return False, str(e)

def place_broker_order(action, volume=0.01, sl=None, tp=None):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(execute_trade_on_broker(action, volume, sl, tp))
    except Exception as e:
        print("Broker Error:", e)

# =========================================================
# MARKET DATA
# =========================================================

def get_bars(interval, limit=10):
    try:
        response = requests.get(URL, params={"interval": interval, "limit": limit}, timeout=10)
        data = response.json()
        return data.get("bars", [])
    except Exception as e:
        return []

def get_latest_closed(interval):
    for candle in get_bars(interval):
        if candle.get("isOpen") == False:
            return candle
    return None

def get_two_closed_15m():
    closed = []
    for candle in get_bars("15m"):
        if candle.get("isOpen") == False:
            closed.append(candle)
        if len(closed) == 2:
            break
    return (closed[0], closed[1]) if len(closed) == 2 else (None, None)

# =========================================================
# RESET FUNCTIONS
# =========================================================

def reset_trade():
    global direction, entry, stop_loss, tp1, tp2, tp1_hit, current_trade, last_manage_15m_time
    direction = entry = stop_loss = tp1 = tp2 = last_manage_15m_time = None
    tp1_hit = False
    current_trade = None

def reset_everything():
    global support, resistance, state, last_1h_time, last_15m_time
    support = resistance = None
    state = "WAITING_SUPPORT_RESISTANCE"
    last_1h_time = last_15m_time = None
    reset_trade()

def start_trade_record():
    global current_trade
    current_trade = {"day": today_name(), "profit": 0, "stop_loss": 0, "status": "OPEN"}

def finish_trade_tp1():
    global current_trade
    if not current_trade: start_trade_record()
    current_trade.update({"profit": 100, "status": "TP1"})
    weekly_results[current_trade["day"]].append({"profit": 100, "stop_loss": 0})
    current_trade = None

def finish_trade_tp2():
    global current_trade
    if not current_trade: start_trade_record()
    current_trade.update({"profit": 150, "status": "TP2"})
    weekly_results[current_trade["day"]].append({"profit": 150, "stop_loss": 0})
    current_trade = None

def finish_trade_sl():
    global current_trade
    if not current_trade: start_trade_record()
    sl_pips = round(abs(entry - stop_loss) * 10)
    current_trade.update({"profit": 0, "stop_loss": sl_pips, "status": "SL"})
    weekly_results[current_trade["day"]].append({"profit": 0, "stop_loss": sl_pips})
    current_trade = None

def finish_trade_breakeven():
    global current_trade
    if not current_trade: return
    current_trade.update({"profit": 100, "status": "TP1_BE"})
    weekly_results[current_trade["day"]].append({"profit": 100, "stop_loss": 0})
    current_trade = None

def set_support_resistance(new_support, new_resistance):
    global support, resistance, state, last_1h_time, last_15m_time
    support, resistance = new_support, new_resistance
    state = "WAITING_1H"
    reset_trade()
    last_1h_time = (get_latest_closed("1h") or {}).get("openTime")
    last_15m_time = (get_latest_closed("15m") or {}).get("openTime")
    send_telegram(f"✅ SUPPORT / RESISTANCE UPDATED\n\n🟦 Support: {support:.2f}\n🟥 Resistance: {resistance:.2f}\n\n🤖 Bot is watching XAUUSD.")

# =========================================================
# TELEGRAM COMMANDS CHECKER (Fast Loop)
# =========================================================

def check_telegram():
    global last_update_id, support, resistance, state
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        res = requests.get(url, params={"offset": last_update_id + 1, "timeout": 2}, timeout=4)
        data = res.json()
        if not data.get("ok"): return

        for update in data.get("result", []):
            last_update_id = update["update_id"]
            msg = update.get("message")
            if not msg or str(msg.get("chat", {}).get("id")) != str(CHAT_ID): continue

            text = msg.get("text", "").strip()

            if text.startswith("/support"):
                parts = text.split()
                if len(parts) == 3:
                    try:
                        ns, nr = float(parts[1]), float(parts[2])
                        if ns < nr: set_support_resistance(ns, nr)
                        else: send_telegram("❌ Support must be below Resistance.")
                    except: send_telegram("❌ Wrong format. Use: /support 4275 4300")
                else: send_telegram("❌ Use: /support 4275 4300")

            elif text == "/cancel":
                reset_everything()
                send_telegram("❌ SUPPORT / RESISTANCE CANCELLED\n\nUse:\n/support 4275 4300")

            elif text == "/status":
                if support is None: send_telegram("⚠️ Support / Resistance not set.\n\n/support 4275 4300")
                else: send_telegram(f"📊 XAUUSD BOT STATUS\n\n🟦 Support: {support:.2f}\n🟥 Resistance: {resistance:.2f}\n🤖 State: {state}")
            elif text == "/start":
                send_telegram("🤖 XAUUSD Bot is active and running!\n\nUse /status or /support [S] [R]")
    except Exception as e:
        pass

# =========================================================
# MARKET STRATEGY LOGIC
# =========================================================

def check_1h():
    global state, last_1h_time, last_15m_time
    candle = get_latest_closed("1h")
    if not candle or candle["openTime"] == last_1h_time: return
    last_1h_time = candle["openTime"]
    close_price = float(candle["close"])

    if close_price >= resistance + 5.0:
        state = "WAITING_BUY_15M"
        last_15m_time = (get_latest_closed("15m") or {}).get("openTime")
        send_telegram("confirmation for Today Buy\n\nwait for Confirmation Candle Buy 15M")
    elif close_price <= support - 5.0:
        state = "WAITING_SELL_15M"
        last_15m_time = (get_latest_closed("15m") or {}).get("openTime")
        send_telegram("confirmation for Today Sell\n\nwait for Confirmation Candle Sell 15M")

def check_buy_confirmation():
    global state, last_15m_time, direction, entry, stop_loss, tp1, tp2, tp1_hit
    current, previous = get_two_closed_15m()
    if not current or not previous or current["openTime"] == last_15m_time: return
    last_15m_time = current["openTime"]

    o, h, l, c = float(current["open"]), float(current["high"]), float(current["low"]), float(current["close"])
    po, pc = float(previous["open"]), float(previous["close"])

    if is_hammer(o, h, l, c) or is_bullish_engulfing(po, pc, o, c):
        direction, entry, stop_loss, tp1, tp2, tp1_hit = "BUY", c, l - 3.0, c + 10.0, c + 15.0, False
        state = "MANAGING_BUY"
        start_trade_record()
        place_broker_order("BUY", volume=0.01, sl=stop_loss, tp=tp2)
        send_telegram(f"🟢 XAUUSD BUY SIGNAL & ORDER PLACED\n\nEntry: {entry:.2f}\nStop: {stop_loss:.2f}\nTP1: {tp1:.2f}\nTP2: {tp2:.2f}")

def check_sell_confirmation():
    global state, last_15m_time, direction, entry, stop_loss, tp1, tp2, tp1_hit
    current, previous = get_two_closed_15m()
    if not current or not previous or current["openTime"] == last_15m_time: return
    last_15m_time = current["openTime"]

    o, h, l, c = float(current["open"]), float(current["high"]), float(current["low"]), float(current["close"])
    po, pc = float(previous["open"]), float(previous["close"])

    if is_shooting_star(o, h, l, c) or is_bearish_engulfing(po, pc, o, c):
        direction, entry, stop_loss, tp1, tp2, tp1_hit = "SELL", c, h + 3.0, c - 10.0, c - 15.0, False
        state = "MANAGING_SELL"
        start_trade_record()
        place_broker_order("SELL", volume=0.01, sl=stop_loss, tp=tp2)
        send_telegram(f"🔴 XAUUSD SELL SIGNAL & ORDER PLACED\n\nEntry: {entry:.2f}\nStop: {stop_loss:.2f}\nTP1: {tp1:.2f}\nTP2: {tp2:.2f}")

def manage_buy():
    global tp1_hit, stop_loss, last_manage_15m_time
    candle = get_latest_closed("15m")
    if not candle or candle["openTime"] == last_manage_15m_time: return
    last_manage_15m_time = candle["openTime"]
    h, l = float(candle["high"]), float(candle["low"])

    if not tp1_hit:
        if l <= stop_loss:
            finish_trade_sl()
            send_telegram("SORRY STOP LOSS ⛔\n\nWaiting for new Support / Resistance.")
            reset_everything()
        elif h >= tp1:
            tp1_hit, stop_loss = True, entry
            send_telegram(f"CONGRATULATIONS 🎉 TP1 Hit!\nNew Stop Loss moved to Entry: {entry:.2f}")
    else:
        if l <= entry:
            finish_trade_breakeven()
            send_telegram("⚠️ Market returned to entry (BreakEven).\nWaiting for new Support / Resistance.")
            reset_everything()
        elif h >= tp2:
            finish_trade_tp2()
            send_telegram("CONGRATULATIONS 🎉 TP2 Hit!\nWaiting for new Support / Resistance.")
            reset_everything()

def manage_sell():
    global tp1_hit, stop_loss, last_manage_15m_time
    candle = get_latest_closed("15m")
    if not candle or candle["openTime"] == last_manage_15m_time: return
    last_manage_15m_time = candle["openTime"]
    h, l = float(candle["high"]), float(candle["low"])

    if not tp1_hit:
        if h >= stop_loss:
            finish_trade_sl()
            send_telegram("SORRY STOP LOSS ⛔\n\nWaiting for new Support / Resistance.")
            reset_everything()
        elif l <= tp1:
            tp1_hit, stop_loss = True, entry
            send_telegram(f"CONGRATULATIONS 🎉 TP1 Hit!\nNew Stop Loss moved to Entry: {entry:.2f}")
    else:
        if h >= entry:
            finish_trade_breakeven()
            send_telegram("⚠️ Market returned to entry (BreakEven).\nWaiting for new Support / Resistance.")
            reset_everything()
        elif l <= tp2:
            finish_trade_tp2()
            send_telegram("CONGRATULATIONS 🎉 TP2 Hit!\nWaiting for new Support / Resistance.")
            reset_everything()

# =========================================================
# MAIN BOT ASYNC LOOP
# =========================================================

async def main_bot():
    global api
    api = MetaApi(API_KEY)
    print("XAUUSD TELEGRAM & METAAPI BOT STARTED SUCCESSFULLY")

    while True:
        try:
            # پشکنینی بەردەوامی تێلیگرام بۆ ئەوەی خێرا وەڵام بداتەوە
            for _ in range(3):
                check_telegram()
                await asyncio.sleep(5)

            if support is not None and resistance is not None:
                if state == "WAITING_1H": check_1h()
                elif state == "WAITING_BUY_15M": check_buy_confirmation()
                elif state == "WAITING_SELL_15M": check_sell_confirmation()
                elif state == "MANAGING_BUY": manage_buy()
                elif state == "MANAGING_SELL": manage_sell()
        except Exception as e:
            print("MAIN ERROR:", e)
            await asyncio.sleep(5)

def run_bot_loop():
    asyncio.run(main_bot())

@app.route('/', methods=['GET'])
def index():
    return "XAUUSD Trading Bot & MetaApi is Running Live!", 200

if __name__ == '__main__':
    t = threading.Thread(target=run_bot_loop, daemon=True)
    t.start()

    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
