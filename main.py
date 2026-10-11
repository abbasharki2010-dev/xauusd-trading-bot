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

API_KEY = "eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiIzODg3ZDJjNmQ3NTNmYjRkNThjMjJlOGUzMGMyZmUzYyIsImFjY2Vzc1J1bGVzIjpbeyJpZCI6InRyYWRpbmctYWNjb3VudC1tYW5hZ2VtZW50LWFwaSIsIm1ldGhvZHMiOlsidHJhZGluZy1hY2NvdW50LW1hbmFnZW1lbnQtYXBpOnJlc3Q6cHVibGljOio6KiJdLCJyb2xlcyI6WyJyZWFkZXIiLCJ3cml0ZXIiXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFhcGktcmVzdC1hcGkiLCJtZXRob2RzIjpbIm1ldGFhcGktYXBpOndzOnB1YmxpYzoqOioiXSwicm9sZXMiOlsicmVhZGVyIiwid3JpdGVyXSwicmVzb3VyY2VzIjpbIio6JFVTRVJfSUQkOioiXX0seyJpZCI6Im1ldGFzdGF0cy1hcGkiLCJtZXRob2RzIjpbIm1ldGFzdGF0cy1hcGk6cmVzdDpub2JsaWM6KiOqIikgZmFsc2UsInRva2VuQiQiOiIyMDIxMDIxMyIsImltcGVyc29uYXRlZCI6ZmFsc2UsInJlYWxVc2VySWQiOiIzODg3ZDJjNmQ3NTNmYjRkNThjMjJlOGUzMGMyZmUzYyIsImlhdCI6MTc5MTczMjg2MywiZXhwIjoxNzk5NTA4ODYzfQ.Pn_ctorbaAXmBlJ6qHCj72Simm6dWPNwFOIOsU4MpGy-4VUYOpEqXr1FI-OJt1pzwjuGqIEn9XmkzIieQ6sB8TTFKIawJWoA6WYqzrWGknsd85pddAeCk0tQN1cViWof5_keCkNqszCbK63XFJHEQ2mH1pdaZg9COCV_VWyBD7xcc3mMzT4Sfm6Pb6Bc952Rry-ksgh1fIteiq27LnFlVtaS_C-b817DPXYTdudaqinwMKPpHHCoUadjbEM8KV69Cyeon27egpQyWLAegk2BucHBQZ4jIuAXX-PdHwneyCX42nBpW_qUk6qB0cgHT6_sjO8Psnj0GQknm3qZplWyGT060g4ciNareE1J8-S0rTSorc6bWwryTjZl2q0sMSCWTT8zMgd1-KFqnrqIbb1RJqXgA0hepfB60LjQGfvEHQ71V0KIjoHeax5HGdHU41pSq3j3Jf2Becqdx7hK0HUH376-836WdkZrSfTRC1-Oo9-fXY0SSTIlkEbAyUuZ7xxhf-e6C6PfZplA3ZFDzBoMvjuMDji-oS-w3hcCnWYcdGAdrvc_qHmfTH9c48z-FGAOMJ2_JuQcRewbycNGmVoNN5jyi5NteNgzM4P6JI3IrHY-KE6CsXKpqaatLha6o55PgjdtHohQw6uq838lXllWg3P37_j-ijno9iU5vxW1Kp4"
ACCOUNT_ID = "3488c28e-d7bf-485d-8d9c-5efb121082b3"

api = None
app = Flask(__name__)
IRAQ_TZ = ZoneInfo("Asia/Baghdad")

support = None
resistance = None
state = "WAITING_SUPPORT_RESISTANCE"

def iraq_now():
    return datetime.now(IRAQ_TZ)

def today_name():
    return iraq_now().strftime("%A")

def send_telegram(message):
    try:
        telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(telegram_url, data={"chat_id": CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print("Telegram ERROR:", e)

# =========================================================
# WEBHOOK ENDPOINT (تێلیگرام ڕاستەوخۆ پەیام دەێرێتە لێرە)
# =========================================================

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    global support, resistance, state
    try:
        data = request.get_json()
        if data and "message" in data:
            msg = data["message"]
            chat_id = str(msg.get("chat", {}).get("id"))
            
            if chat_id == str(CHAT_ID):
                text = msg.get("text", "").strip()

                if text == "/start":
                    send_telegram("🤖 XAUUSD Bot is active and running!\n\nUse /status or /support [S] [R]")
                elif text.startswith("/support"):
                    parts = text.split()
                    if len(parts) == 3:
                        try:
                            ns, nr = float(parts[1]), float(parts[2])
                            if ns < nr:
                                support, resistance = ns, nr
                                state = "WAITING_1H"
                                send_telegram(f"✅ SUPPORT / RESISTANCE UPDATED\n\n🟦 Support: {support:.2f}\n🟥 Resistance: {resistance:.2f}")
                            else:
                                send_telegram("❌ Support must be below Resistance.")
                        except:
                            send_telegram("❌ Wrong format. Use: /support 4275 4300")
                elif text == "/status":
                    if support is None:
                        send_telegram("⚠️ Support / Resistance not set.\n\nUse: /support 4275 4300")
                    else:
                        send_telegram(f"📊 STATUS\nSupport: {support}\nResistance: {resistance}\nState: {state}")
                elif text == "/cancel":
                    support = resistance = None
                    state = "WAITING_SUPPORT_RESISTANCE"
                    send_telegram("❌ Cancelled successfully.")
        return jsonify({"ok": True}), 200
    except Exception as e:
        print("Webhook error:", e)
        return jsonify({"ok": False}), 500

@app.route('/', methods=['GET'])
def index():
    return "XAUUSD Trading Bot & MetaApi is Running Live!", 200

# =========================================================
# SETTING UP WEBHOOK AUTOMATICALLY ON RAILWAY
# =========================================================

def setup_webhook():
    time.sleep(3) # چاوەڕێی هەڵبوونی سێرڤەر دەکات
    railway_domain = os.getenv("RAILWAY_STATIC_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if railway_domain:
        webhook_url = f"https://{railway_domain}/{BOT_TOKEN}"
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}"
        res = requests.get(url)
        print("Webhook Setup Response:", res.text)

if __name__ == '__main__':
    # دەستپێکردنی تۆر لە پشتەوە بۆ ڕێکخستنی Webhook
    threading.Thread(target=setup_webhook, daemon=True).start()
    
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
