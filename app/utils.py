import os
import asyncio
import requests
from dotenv import load_dotenv

# Load environment variables
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=ENV_PATH)

def get_chat_ids() -> list:
    """
    Retrieves and parses the list of comma-separated Telegram chat IDs.
    """
    raw_chat_ids = os.getenv("TELEGRAM_CHAT_ID")
    if not raw_chat_ids:
        return []
    return [c.strip() for c in raw_chat_ids.split(",") if c.strip()]

def get_platform_tokens(platform: str) -> list:
    """
    Retrieves and parses the list of platform-specific bot tokens.
    """
    platform_upper = platform.upper()
    if "NEWMYNTRA" in platform_upper:
        token_str = os.getenv("NEW_MYNTRA_BOT_TOKEN")
    elif "OLDMYNTRA" in platform_upper:
        token_str = os.getenv("OLD_MYNTRA_BOT_TOKEN")
    elif "AMAZON" in platform_upper:
        token_str = os.getenv("AMAZON_BOT_TOKEN")
    else:
        token_str = os.getenv("NEW_MYNTRA_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        
    if not token_str:
        return []
    return [t.strip() for t in token_str.split(",") if t.strip()]

async def send_telegram_alert(message: str, platform: str) -> bool:
    """
    Asynchronously sends a Telegram notification using account-specific bot tokens.
    Supports sending to multiple users concurrently.
    Uses HTML parsing mode. Runs the synchronous requests call in a background thread.
    """
    chat_ids = get_chat_ids()
    tokens = get_platform_tokens(platform)

    if not tokens or not chat_ids:
        print(f"[Telegram] [WARNING] Credentials missing for platform '{platform}'. Alert skipped.")
        print(f"[Telegram Alert Log - {platform}]:\n{message}")
        return False

    tasks = []

    async def send_to_single_chat(chat_id: str, token: str) -> bool:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
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
                print(f"[Telegram] Alert sent successfully for {platform} to chat ID {chat_id}")
                return True
            else:
                print(f"[Telegram] [ERROR] Failed to send alert for {platform} to chat ID {chat_id}: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"[Telegram] [ERROR] Exception occurred while sending alert to chat ID {chat_id}: {e}")
            return False

    for i, chat_id in enumerate(chat_ids):
        # Match each chat_id with the corresponding bot token, falling back to the first token if there are fewer tokens than chat IDs
        token = tokens[i] if i < len(tokens) else tokens[0]
        tasks.append(send_to_single_chat(chat_id, token))

    results = await asyncio.gather(*tasks)
    return any(results)

def send_telegram_message(message: str):
    """
    Synchronous fallback for sending Markdown alerts.
    Supports sending to multiple users.
    """
    chat_ids = get_chat_ids()
    token_str = os.getenv("NEW_MYNTRA_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not token_str or not chat_ids:
        print("[Telegram] [WARNING] Missing credentials. Alert skipped.")
        print(f"[Telegram Message Log]:\n{message}")
        return False

    tokens = [t.strip() for t in token_str.split(",") if t.strip()]
    if not tokens:
        print("[Telegram] [WARNING] Missing credentials. Alert skipped.")
        return False

    success = False
    for i, chat_id in enumerate(chat_ids):
        token = tokens[i] if i < len(tokens) else tokens[0]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                print(f"[Telegram] Alert sent successfully to chat ID {chat_id}")
                success = True
            else:
                print(f"[Telegram] [ERROR] Failed to send alert to chat ID {chat_id}: {response.text}")
        except Exception as e:
            print(f"[Telegram] [ERROR] Exception occurred sending to chat ID {chat_id}: {e}")

    return success
