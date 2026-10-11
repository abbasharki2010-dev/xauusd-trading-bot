import os
import requests
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
from flask import Flask
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

support = None
resistance = None
state = "WAITING_SUPPORT_RESISTANCE"
last_1h_time = None
last_15m_time = None
last_manage_15m_time = None
last_update_id = 0

def iraq_now():
    return datetime.now(IRAQ_TZ)

def send_telegram(message):
    try:
        telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(telegram_url, data={"chat_id": CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print("Telegram ERROR:", e)

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
            print(f"Received message: {text}")

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
    except Exception as e:
        print("Check telegram error:", e)

@app.route('/')
def index():
    return "Bot is running!", 200

async def main():
    # سڕینەوەی کێشەی وێبهایوک لە سەرەتاوە
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true", timeout=5)
        print("Webhook cleared successfully.")
    except Exception as e:
        print("Error clearing webhook:", e)

    print("BOT STARTED LOOPING...")
    while True:
        try:
            check_telegram()
        except Exception as e:
            print("Loop error:", e)
        await asyncio.sleep(2)

if __name__ == '__main__':
    import threading
    # دەستپێکردنی سێرڤەری فڵەسک لە پاشەوە
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), debug=False, use_reloader=False), daemon=True).start()
    
    # کارپێکردنی لۆپی سەرەکی بووت
    asyncio.run(main())
