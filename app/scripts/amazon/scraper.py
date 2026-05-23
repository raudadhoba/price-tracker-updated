import os
import json
import re
import sys
import uuid
import asyncio
from datetime import datetime, timezone, timedelta, time as datetime_time
from typing import Dict, List, Any

# Reconfigure stdout/stderr to support UTF-8 emojis on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add the app directory to the system path to allow absolute imports
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
if APP_DIR not in sys.path:
    sys.path.append(APP_DIR)

from utils import send_telegram_alert
from browser_manager import run_with_playwright

# Data Paths
DATA_DIR = os.path.join(APP_DIR, "data")
PRODUCTS_PATH = os.path.join(DATA_DIR, "amazon_products.json")


def extract_asin(url: str) -> str | None:
    """Extracts the unique 10-character ASIN from an Amazon product URL."""
    if not url:
        return None
    patterns = [
        r"/dp/([A-Z0-9]{10})",
        r"/gp/product/([A-Z0-9]{10})",
        r"/product/([A-Z0-9]{10})",
        r"/d/([A-Z0-9]{10})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return None


def _parse_price(text: str) -> float | None:
    """Clean Indian Rupee price text and return float or None."""
    if not text:
        return None
    clean = text.replace("₹", "").replace("Rs.", "").replace("Rs", "").replace(",", "").strip()
    m = re.search(r"\d+\.?\d*", clean)
    if m:
        try:
            return float(m.group())
        except:
            pass
    return None


async def amazon_price_check_raw() -> dict:
    """Runs the scraping flow for Amazon cart-based pricing alerts."""
    products = []
    try:
        if os.path.exists(PRODUCTS_PATH):
            with open(PRODUCTS_PATH, "r", encoding="utf-8") as f:
                products = json.load(f)
            if not isinstance(products, list):
                products = []
            
            # Ensure every product has an id, remove prices history, and keep database clean
            for p in products:
                if "id" not in p:
                    asin = extract_asin(p.get("url"))
                    p["id"] = asin if asin else str(uuid.uuid4())
                if "prices" in p:
                    del p["prices"]
                
                # Pop all redundant keys
                unnecessary_keys = ["last_checked_price", "lowest_notified_price", "last_scanned_price"]
                for key in unnecessary_keys:
                    p.pop(key, None)
                
    except Exception as e:
        print(f"[Amazon] ERROR reading products: {e}")
        return {"error": f"Error reading products: {e}"}

    print(f"[Amazon] Found {len(products)} tracked products")

    async def amazon_flow(context, page):
        # Refresh the home page first to bind session cookies loaded by Browser Manager
        print("[Amazon] Refreshing home page to establish session context...")
        try:
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"[Amazon] ⚠️ Page reload issue (continuing anyway): {e}")

        # Navigate to Amazon Cart View URL where price change messages are rendered
        cart_url = "https://www.amazon.in/gp/cart/view.html?ref_=nav_cart"
        print(f"[Amazon] Navigating to Cart view: {cart_url}...")
        try:
            await page.goto(cart_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"[Amazon] ⚠️ Navigation timed out or completed with warning. Proceeding to parse: {e}")
            await page.wait_for_timeout(3000)

        # Scrape price change banners from the page DOM
        print("[Amazon] Scoping price alerts from cart page...")
        js_code = """
        () => {
            const items = [];
            const messageEls = document.querySelectorAll('[data-feature-id="single-imb-message"]');
            for (const msg of messageEls) {
                const container = msg.closest('li') || msg.parentElement;
                const inputEl = container ? container.querySelector('input[name="imb-type"]') : null;
                const type = inputEl ? inputEl.value : null;
                
                const linkEl = msg.querySelector('a');
                const href = linkEl ? linkEl.getAttribute('href') : null;
                
                const titleEl = msg.querySelector('.sc-product-title');
                const title = titleEl ? titleEl.textContent.trim() : '';
                
                const priceElements = msg.querySelectorAll('.sc-product-price');
                const prices = Array.from(priceElements).map(el => el.textContent.trim());
                
                items.push({
                    type: type,
                    href: href,
                    title: title,
                    prices: prices
                });
            }
            return items;
        }
        """
        scraped_raw = []
        try:
            scraped_raw = await page.evaluate(js_code)
        except Exception as e:
            print(f"[Amazon] [ERROR] Failed to run browser DOM parser: {e}")
            
        print(f"[Amazon] Extracted {len(scraped_raw)} raw price alert items from Cart.")

        # Map scraped price decreases by ASIN
        scraped_decreases = {}
        for item in scraped_raw:
            type_val = item.get("type")
            href = item.get("href")
            title = item.get("title", "")
            prices = item.get("prices", [])

            # We only track priceDecrease drops
            if type_val != "priceDecrease":
                continue

            asin = extract_asin(href)
            if not asin:
                continue

            from_price = _parse_price(prices[0]) if len(prices) > 0 else None
            to_price = _parse_price(prices[1]) if len(prices) > 1 else None

            if to_price is not None:
                scraped_decreases[asin] = {
                    "asin": asin,
                    "title": title,
                    "href": href,
                    "from_price": from_price,
                    "to_price": to_price
                }
                print(f"  [Scraped Drop] ASIN: {asin} | {title[:45]}... | ₹{from_price} -> ₹{to_price}")

        # Scan each configured tracked product
        results = []
        scan_time = datetime.now(timezone.utc).isoformat()
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        today_str = datetime.now(ist_tz).strftime("%Y-%m-%d")

        for i, product in enumerate(products):
            name = product.get("name", "Unknown Product")
            url = product.get("url")
            raw_target = product.get("desired_price") or product.get("target_price")
            
            try:
                target_price = float(raw_target) if raw_target is not None else None
            except:
                target_price = None

            if not url:
                print(f"  [Amazon] Product {i+1} skipped (no URL)")
                continue

            asin = extract_asin(url)
            if not asin:
                print(f"  [Amazon] Product {i+1} ('{name[:30]}') skipped: could not resolve ASIN from URL.")
                continue

            print(f"\n[Amazon] Processing Tracked Product {i+1}/{len(products)}: '{name}' (ASIN: {asin})")
            
            match = scraped_decreases.get(asin)
            
            pdp_price = None
            best_price = None
            mrp = None
            hit_target = False
            drop_amount = None
            drop_pct = None
            trigger_immediate_alert = False

            if match:
                pdp_price = match["from_price"]
                best_price = match["to_price"]
                mrp = match["from_price"]
                compare_price = best_price
                
                print(f"  [Match] Cart alert found! Price dropped from ₹{pdp_price} to ₹{best_price} (Target: ₹{target_price})")
                
                if target_price is not None and target_price > 0:
                    drop_amount = round(target_price - compare_price, 2)
                    drop_pct = round((drop_amount / target_price) * 100, 1)

                    if compare_price <= target_price:
                        hit_target = True
                        
                        last_notified_price = product.get("last_notified_price")
                        if last_notified_price is not None:
                            try:
                                last_notified_price = float(last_notified_price)
                            except:
                                last_notified_price = None
                        last_notified_date = product.get("last_notified_date")

                        should_notify = False
                        if last_notified_date != today_str:
                            should_notify = True
                        elif last_notified_price is not None and compare_price < last_notified_price:
                            should_notify = True
                        
                        if should_notify:
                            trigger_immediate_alert = True
                            product["last_notified_price"] = compare_price
                            product["last_notified_date"] = today_str
                            print(f"  [Alert] 🔔 Target hit! ₹{compare_price} vs target ₹{target_price} (Last notified: {last_notified_price} on {last_notified_date})")
                        else:
                            print(f"  [Alert] ℹ️ Already hit and notified at ₹{last_notified_price} today. No new alert.")
            else:
                print("  [Compare] No price drop alert found in cart. Price is unchanged or no active alerts.")
                best_price = product.get("best_price")
                pdp_price = product.get("pdp_price")
                mrp = product.get("mrp")
                compare_price = best_price

            # Update product attributes
            product["pdp_price"] = pdp_price
            product["best_price"] = best_price
            product["mrp"] = mrp
            product["scan_status"] = "scanned"
            product["last_scanned_at"] = scan_time

            results.append({
                "id": product.get("id"),
                "name": name,
                "url": url,
                "target_price": target_price,
                "pdp_price": pdp_price,
                "mrp": mrp,
                "best_price": best_price,
                "image": product.get("image"),
                "drop_amount": drop_amount,
                "drop_pct": drop_pct,
                "hit_target": hit_target,
                "trigger_immediate": trigger_immediate_alert,
                "scan_status": product["scan_status"]
            })

        # Save product updates back to amazon_products.json
        try:
            with open(PRODUCTS_PATH, "w", encoding="utf-8") as f:
                json.dump(products, f, indent=4)
            print("[Amazon] ✅ Updated amazon_products.json with latest scan data")
        except Exception as e:
            print(f"[Amazon] ⚠️ Failed to save amazon_products.json: {e}")

        # 1. SEND IMMEDIATE TELEGRAM ALERTS
        new_hits = [r for r in results if r.get("trigger_immediate")]
        if new_hits:
            print(f"[Amazon] Sending immediate Telegram alert for {len(new_hits)} NEW target hit(s)...")
            lines = [f"🛍️ <b>Amazon Price Alert — New Lowest Price!</b>\n"]
            for r in new_hits:
                bp = r["best_price"]
                lines.append(
                    f'🟢 <a href="{r["url"]}">{r["name"]}</a>\n'
                    f'   🔥 Price: ₹{bp}\n'
                    f'   🎯 Target: ₹{r["target_price"]} | Drop: ₹{r["drop_amount"]} ({r["drop_pct"]}%)\n'
                )
            await send_telegram_alert("\n\n".join(lines), platform="AMAZON")
        else:
            print("[Amazon] No NEW immediate alerts triggered — skipping instant alert")

        print(f"\n[Amazon] Done — processed {len(results)} products")
        return {"processed_products": results}

    return await run_with_playwright(amazon_flow, platform="amazon")


async def track_prices():
    """Main entry point called by engine."""
    print("\n[Amazon] === Starting Amazon price checking cycle ===")
    try:
        res = await amazon_price_check_raw()
        print(f"[Amazon] Cycle complete. Result keys: {list(res.keys())}")
    except Exception as e:
        print(f"[Amazon] ERROR running Amazon price check: {e}")
    print("[Amazon] === Amazon price checking cycle complete ===")


def track_prices_sync():
    """Synchronous CLI wrapper."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(track_prices())
    finally:
        loop.close()


if __name__ == "__main__":
    track_prices_sync()
