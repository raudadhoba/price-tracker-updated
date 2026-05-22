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
PRODUCTS_PATH = os.path.join(DATA_DIR, "amazon_products.json")


def load_products():
    if not os.path.exists(PRODUCTS_PATH):
        print(f"[WARNING] amazon_products.json not found at {PRODUCTS_PATH}.")
        return []
    try:
        with open(PRODUCTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load amazon_products.json: {e}")
        return []


def save_products(products):
    try:
        with open(PRODUCTS_PATH, "w", encoding="utf-8") as f:
            json.dump(products, f, indent=2)
        print("[INFO] Successfully updated amazon_products.json with latest prices.")
    except Exception as e:
        print(f"[ERROR] Failed to save amazon_products.json: {e}")


async def extract_price(page) -> float | None:
    """Extracts the product price from Amazon page asynchronously."""
    try:
        selectors = [
            "span.a-price-whole",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "span.a-offscreen",
            "#corePrice_feature_div .a-price-whole"
        ]
        
        price_selector = None
        for sel in selectors:
            if await page.is_visible(sel):
                price_selector = sel
                break
                
        if not price_selector:
            print("[WARNING] Could not find any standard Amazon price selectors on this page.")
            return None
            
        price_text = await page.locator(price_selector).first.inner_text()
        
        cleaned_price = "".join(c for c in price_text if c.isdigit() or c == ".")
        if cleaned_price:
            return float(cleaned_price)
        return None
    except Exception as e:
        print(f"[ERROR] Failed to extract Amazon price: {e}")
        return None


async def track_prices():
    """Asynchronously tracks Amazon product prices."""
    products = load_products()
    if not products:
        print("[INFO] No Amazon products configured for tracking.")
        return
        
    print(f"\n[AMAZON] Starting tracking for {len(products)} product(s)...")

    async def amazon_flow(context, page):
        # Scan each product (Domain and cookies are already bound by Browser Manager)
        for i, product in enumerate(products):
            if i > 0:
                await page.wait_for_timeout(3000)

            name = product.get("name", "Unknown Product")
            url = product.get("url")
            target_price = product.get("target_price") or product.get("desired_price") or 0.0
            
            if not url:
                print(f"[Amazon] Skipping '{name}' due to missing URL.")
                continue
                
            print(f"[Amazon] Scoping product {i+1}/{len(products)}: '{name}'")
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                current_price = await extract_price(page)
                
                if current_price is None:
                    print(f"[Amazon] [ERROR] Could not resolve price for '{name}'")
                    continue
                    
                print(f"[Amazon] Price extracted: Rs. {current_price} | Target: Rs. {target_price}")
                product["last_checked_price"] = current_price
                product["last_scanned_price"] = current_price
                
                if current_price <= target_price:
                    message = (
                        f"🚨 <b>AMAZON PRICE DROP ALERT!</b> 🚨\n\n"
                        f"🛍️ <b>Product:</b> {name}\n"
                        f"📉 <b>Current Price:</b> ₹{current_price}\n"
                        f"🎯 <b>Target Price:</b> ₹{target_price}\n"
                        f"🔗 <a href='{url}'>Buy Now</a>"
                    )
                    await send_telegram_alert(message, platform="AMAZON")
                else:
                    print("[Amazon] Price is not below target price. No alert sent.")
            except Exception as e:
                print(f"[Amazon] [ERROR] Amazon scraper crashed on '{name}': {e}")

    try:
        await run_with_playwright(amazon_flow, platform="amazon")
    except Exception as outer_e:
        print(f"[Amazon] Outer error: {outer_e}")

    save_products(products)
    print("[AMAZON] Scraping run completed successfully.")


def track_prices_sync():
    """Synchronous wrapper for Amazon scraper."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(track_prices())
    finally:
        loop.close()


if __name__ == "__main__":
    track_prices_sync()
