import os
import json
import sys
import asyncio
from playwright.async_api import async_playwright

# Reconfigure stdout/stderr to support UTF-8 emojis on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)

from utils import send_telegram_alert
from browser_manager import run_with_playwright

# Data Paths
DATA_DIR = os.path.join(APP_DIR, "data")
PRODUCTS_PATH = os.path.join(DATA_DIR, "flipkart_products.json")


def load_products():
    if not os.path.exists(PRODUCTS_PATH):
        print(f"[WARNING] flipkart_products.json not found at {PRODUCTS_PATH}.")
        return []
    try:
        with open(PRODUCTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load flipkart_products.json: {e}")
        return []


def save_products(products):
    try:
        with open(PRODUCTS_PATH, "w", encoding="utf-8") as f:
            json.dump(products, f, indent=2)
        print("[INFO] Successfully updated flipkart_products.json with latest prices.")
    except Exception as e:
        print(f"[ERROR] Failed to save flipkart_products.json: {e}")


async def extract_price(page) -> float | None:
    """Extracts the product price from Flipkart page asynchronously."""
    try:
        selectors = [
            ".Nx9Zqj",
            "._30jeq3",
            "div.Nx9Zqj",
            "._30jeq3._16JkGQ"
        ]
        
        price_selector = None
        for sel in selectors:
            if await page.is_visible(sel):
                price_selector = sel
                break
                
        if not price_selector:
            print("[WARNING] Could not find any standard Flipkart price selectors on this page.")
            return None
            
        price_text = await page.locator(price_selector).first.inner_text()
        
        cleaned_price = "".join(c for c in price_text if c.isdigit() or c == ".")
        if cleaned_price:
            return float(cleaned_price)
        return None
    except Exception as e:
        print(f"[ERROR] Failed to extract Flipkart price: {e}")
        return None


async def track_prices():
    """Asynchronously tracks Flipkart product prices."""
    products = load_products()
    if not products:
        print("[INFO] No Flipkart products configured for tracking.")
        return
        
    print(f"\n[FLIPKART] Starting tracking for {len(products)} product(s)...")

    async def flipkart_flow(context, page):
        # Scan each product (Domain and cookies are already bound by Browser Manager)
        for i, product in enumerate(products):
            if i > 0:
                await page.wait_for_timeout(3000)

            name = product.get("name", "Unknown Product")
            url = product.get("url")
            target_price = product.get("target_price") or product.get("desired_price") or 0.0
            
            if not url:
                print(f"[Flipkart] Skipping '{name}' due to missing URL.")
                continue
                
            print(f"[Flipkart] Scoping product {i+1}/{len(products)}: '{name}'")
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                current_price = await extract_price(page)
                
                if current_price is None:
                    print(f"[Flipkart] [ERROR] Could not resolve price for '{name}'")
                    continue
                    
                print(f"[Flipkart] Price extracted: Rs. {current_price} | Target: Rs. {target_price}")
                product["last_checked_price"] = current_price
                product["last_scanned_price"] = current_price
                
                if current_price <= target_price:
                    message = (
                        f"🚨 <b>FLIPKART PRICE DROP ALERT!</b> 🚨\n\n"
                        f"🛍️ <b>Product:</b> {name}\n"
                        f"📉 <b>Current Price:</b> ₹{current_price}\n"
                        f"🎯 <b>Target Price:</b> ₹{target_price}\n"
                        f"🔗 <a href='{url}'>Buy Now</a>"
                    )
                    await send_telegram_alert(message, platform="FLIPKART")
                else:
                    print("[Flipkart] Price is not below target price. No alert sent.")
            except Exception as e:
                print(f"[Flipkart] [ERROR] Flipkart scraper crashed on '{name}': {e}")

    try:
        await run_with_playwright(flipkart_flow, platform="flipkart")
    except Exception as outer_e:
        print(f"[Flipkart] Outer error: {outer_e}")

    save_products(products)
    print("[FLIPKART] Scraping run completed successfully.")


def track_prices_sync():
    """Synchronous wrapper for Flipkart scraper."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(track_prices())
    finally:
        loop.close()


if __name__ == "__main__":
    track_prices_sync()
