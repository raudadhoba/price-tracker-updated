import os
import asyncio
import requests
from dotenv import load_dotenv

# Load environment variables
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=ENV_PATH)

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def send_telegram_alert(message: str, platform: str) -> bool:
    """
    Asynchronously sends a Telegram notification using account-specific bot tokens.
    Uses HTML parsing mode. Runs the synchronous requests call in a background thread.
    """
    # Select the appropriate bot token based on the account/platform prefix
    platform_upper = platform.upper()
    if "NEWMYNTRA" in platform_upper:
        token = os.getenv("NEW_MYNTRA_BOT_TOKEN")
    elif "OLDMYNTRA" in platform_upper:
        token = os.getenv("OLD_MYNTRA_BOT_TOKEN")
    else:
        # Fallback to general bot token or NEW Myntra token
        token = os.getenv("NEW_MYNTRA_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

    if not token or not TELEGRAM_CHAT_ID:
        print(f"[Telegram] [WARNING] Credentials missing for platform '{platform}'. Alert skipped.")
        print(f"[Telegram Alert Log - {platform}]:\n{message}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    try:
        # Run synchronous post request in a separate thread so it doesn't block the async loop
        response = await asyncio.to_thread(
            lambda: requests.post(url, json=payload, timeout=15)
        )
        
        if response.status_code == 200:
            print(f"[Telegram] Alert sent successfully for {platform}")
            return True
        else:
            print(f"[Telegram] [ERROR] Failed to send alert: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"[Telegram] [ERROR] Exception occurred while sending alert: {e}")
        return False

def send_telegram_message(message: str):
    """
    Synchronous fallback for sending Markdown alerts.
    """
    token = os.getenv("NEW_MYNTRA_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not TELEGRAM_CHAT_ID:
        print("[Telegram] [WARNING] Missing credentials. Alert skipped.")
        print(f"[Telegram Message Log]:\n{message}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[Telegram] Alert sent successfully.")
            return True
        else:
            print(f"[Telegram] [ERROR] Failed to send alert: {response.text}")
            return False
    except Exception as e:
        print(f"[Telegram] [ERROR] Exception occurred: {e}")
        return False
