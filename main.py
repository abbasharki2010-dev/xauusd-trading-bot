
import os
import time
import requests

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

URL = f"https://api.telegram.org/bot{TOKEN}/"

offset = 0

print("XAUUSD Telegram Bot is running!")

while True:
    try:
        response = requests.get(
            URL + "getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35
        )
        response.raise_for_status()
        data = response.json()

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text", "").strip()

            if not chat_id or not text:
                continue

            if text.lower() == "/start":
                reply = (
                    "🟡 XAUUSD Bot\n"
                    "سڵاو! بۆتەکە چالاکە.\n\n"
                    "بۆ ناردنی سەپۆرت و ڕیزیستەنس بنووسە:\n"
                    "Support 4275\n"
                    "Resistance 4300"
                )
            elif text.lower().startswith("support") or text.lower().startswith("resistance"):
                reply = (
                    "✅ پەیامەکەت وەرگیرا.\n"
                    "ئەم وەشانە تەنها پەیام وەردەگرێت؛ "
                    "هێشتا شیکردنەوەی بازاڕی تێدا نییە."
                )
            else:
                reply = (
                    "پەیامەکەت وەرگیرا. بۆ دەستپێکردن /start بنێرە."
                )

            requests.post(
                URL + "sendMessage",
                json={"chat_id": chat_id, "text": reply},
                timeout=15
            ).raise_for_status()

    except Exception as error:
        print("Error:", error)
        time.sleep(5)
