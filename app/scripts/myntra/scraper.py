import os
import json
import re
import sys
import uuid
import random as _random
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
PRODUCTS_PATH = os.path.join(DATA_DIR, "myntra_products.json")


def _parse_price(text: str) -> float | None:
    """Clean price text and return float or None."""
    if not text:
        return None
    clean = text.replace("\u20b9", "").replace("Rs.", "").replace("Rs", "").replace(",", "").strip()
    m = re.search(r"[\d]+\.?\d*", clean)
    if m:
        try:
            return float(m.group())
        except:
            pass
    return None


async def myntra_price_check_raw(account: str = "new") -> dict:
    """Runs the scraping flow for Myntra under the specified account ('new' or 'old')."""
    # Load products from products file
    products = []
    try:
        if os.path.exists(PRODUCTS_PATH):
            with open(PRODUCTS_PATH, "r") as f:
                products = json.load(f)
            if not isinstance(products, list):
                products = []
            
            # Ensure every product has an id and remove prices history to keep storage clean
            for p in products:
                if "id" not in p:
                    p["id"] = str(uuid.uuid4())
                if "prices" in p:
                    del p["prices"]
                
    except Exception as e:
        print(f"[Myntra] [{account.upper()}] ERROR reading products: {e}")
        return {"error": f"Error reading products: {e}"}

    print(f"[Myntra] [{account.upper()}] Found {len(products)} tracked products")

    if not products:
        print(f"[Myntra] [{account.upper()}] No products to scan")
        return {"message": "No Myntra products tracked"}

    async def myntra_flow(context, page):
        results = []
        scan_time = datetime.now(timezone.utc).isoformat()
        # Set up dynamic IST date for today's price drop alert deduplication
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        today_str = datetime.now(ist_tz).strftime("%Y-%m-%d")

        # Scan each product
        for i, product in enumerate(products):
            # Random delay between products to avoid bot detection
            if i > 0:
                wait_s = _random.randint(8, 15)
                print(f"  [Myntra] [{account.upper()}] Waiting {wait_s}s before next product...")
                await page.wait_for_timeout(wait_s * 1000)

            url = product.get("url")
            if not url:
                print(f"[Myntra] [{account.upper()}] Product {i+1} — skipped (no URL)")
                continue

            print(f"\n[Myntra] [{account.upper()}] Product {i+1}/{len(products)} — Opening {url}")
            try:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000, referer="https://www.myntra.com/")
                except Exception as e:
                    # If it times out, the HTML might still be loaded enough to parse prices
                    print(f"  [Myntra] [{account.upper()}] ⚠️ Load timeout ({str(e).splitlines()[0]}), attempting to scrape anyway...")
                
                # Give JS an extra moment to render prices in the DOM
                await page.wait_for_timeout(3000)
            except Exception as e:
                print(f"  [Myntra] [{account.upper()}] ⚠️ Failed to load {url} (Error: {e}) - Skipping product")
                product["scan_status"] = "failed"
                product["last_scanned_at"] = scan_time
                results.append({
                    "id": product.get("id"),
                    "name": product.get("name"),
                    "url": url,
                    "target_price": None,
                    "pdp_price": None,
                    "mrp": None,
                    "best_price": None,
                    "image": product.get("image"),
                    "drop_amount": None,
                    "drop_pct": None,
                    "hit_target": False,
                    "scan_status": "failed",
                })
                continue

            # ─ Extract PDP Price (selling price) ─────────────────────
            pdp_price = None
            pdp_el = await page.query_selector(".pdp-price strong")
            if pdp_el:
                pdp_text = await pdp_el.inner_text()
                pdp_price = _parse_price(pdp_text)
                print(f"  [PDP] Price: {pdp_price} (raw: '{pdp_text}')")
            else:
                print("  [PDP] Price element not found")

            # ─ Extract MRP ───────────────────────────────────────────
            mrp = None
            mrp_el = await page.query_selector(".pdp-mrp s")
            if mrp_el:
                mrp_text = await mrp_el.inner_text()
                mrp = _parse_price(mrp_text)
                print(f"  [MRP] Price: {mrp} (raw: '{mrp_text}')")
            else:
                print("  [MRP] MRP element not found")

            # ─ Extract Best Price ────────────────────────────────────
            best_price = None
            offer_titles = await page.query_selector_all(".pdp-offers-offerTitle b")
            for title_el in offer_titles:
                title_text = await title_el.inner_text()
                if "Best Price" in title_text:
                    best_price = _parse_price(title_text)
                    print(f"  [Best] Price: {best_price} (raw: '{title_text}')")
                    break
                    
            if best_price is None:
                print("  [Best] Price element not found (or 'Best Price:' text missing)")

            # ─ Compare best_price with target ─
            raw_target = product.get("desired_price") or product.get("target_price")
            try:
                target_price = float(raw_target) if raw_target is not None else None
            except:
                target_price = None
                
            hit_target = False
            drop_amount = None
            drop_pct = None
            compare_price = best_price if best_price is not None else pdp_price
            trigger_immediate_alert = False

            # Get last notified price and date
            last_notified_price = product.get("last_notified_price")
            if last_notified_price is not None:
                try:
                    last_notified_price = float(last_notified_price)
                except:
                    last_notified_price = None
            
            last_notified_date = product.get("last_notified_date")  # format: "YYYY-MM-DD"

            if compare_price is not None and target_price is not None and target_price > 0:
                drop_amount = round(target_price - compare_price, 2)
                drop_pct = round((drop_amount / target_price) * 100, 1)
                
                # Check if it hits the target price
                if compare_price <= target_price:
                    hit_target = True
                    
                    # Deciding whether to send a notification:
                    # 1. We haven't notified today (last_notified_date != today_str)
                    # 2. Or, we already notified today, but the price decreased further below the last notified price
                    should_notify = False
                    if last_notified_date != today_str:
                        should_notify = True
                    elif last_notified_price is not None and compare_price < last_notified_price:
                        should_notify = True
                    
                    if should_notify:
                        trigger_immediate_alert = True
                        product["last_notified_price"] = compare_price
                        product["last_notified_date"] = today_str
                        product["lowest_notified_price"] = compare_price
                        print(f"  [Alert] 🔔 NEW target hit! {compare_price} vs target {target_price} (Last notified: {last_notified_price} on {last_notified_date})")
                    else:
                        print(f"  [Alert] ℹ️ Already hit and notified at {last_notified_price} today. No new alert.")
                else:
                    status = "[above]"
                    print(f"  [Compare] Best {compare_price} vs Target {target_price} | diff {drop_amount} ({drop_pct}%) | {status}")

            # ─ Update product state ──────────────────────────
            product["pdp_price"] = pdp_price
            product["best_price"] = best_price
            product["mrp"] = mrp
            product["last_scanned_price"] = compare_price
            product["last_checked_price"] = compare_price
            product["scan_status"] = "scanned" if compare_price is not None else "failed"
            product["last_scanned_at"] = scan_time

            results.append({
                "id": product.get("id"),
                "name": product.get("name"),
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
                "scan_status": product["scan_status"],
            })

        # Save product updates back to products.json
        try:
            with open(PRODUCTS_PATH, "w") as f:
                json.dump(products, f, indent=4)
            print(f"[Myntra] [{account.upper()}] ✅ Updated myntra_products.json with latest scan data")
        except Exception as e:
            print(f"[Myntra] [{account.upper()}] ⚠️ Failed to save myntra_products.json: {e}")

        # 1. SEND IMMEDIATE TELEGRAM ALERTS
        new_hits = [r for r in results if r.get("trigger_immediate")]
        if new_hits:
            print(f"[Myntra] [{account.upper()}] Sending immediate Telegram alert for {len(new_hits)} NEW target hit(s)...")
            lines = [f"🛍️ <b>Myntra Price Alert — New Lowest Price! [{account.upper()} ACCOUNT]</b>\n"]
            for r in new_hits:
                bp = r["best_price"] if r["best_price"] is not None else r["pdp_price"]
                lines.append(
                    f'🟢 <a href="{r["url"]}">{r["name"]}</a>\n'
                    f'   🔥 Price: ₹{bp}\n'
                    f'   🎯 Target: ₹{r["target_price"]} | Drop: ₹{r["drop_amount"]} ({r["drop_pct"]}%)\n'
                )
            await send_telegram_alert("\n\n".join(lines), platform=f"{account.upper()}MYNTRA")
        else:
            print(f"[Myntra] [{account.upper()}] No NEW immediate alerts triggered — skipping instant alert")

        # 2. CHECK & SEND TWICE DAILY DIGEST
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        now_dt = datetime.now(ist_tz)
        today_str = now_dt.strftime("%Y-%m-%d")
        now_time = now_dt.time()
        
        digest_state_path = os.path.join(DATA_DIR, f"myntra_{account}_digest_state.json")
        digest_state = {"last_1201am": None, "last_0700am": None}
        
        try:
            if os.path.exists(digest_state_path):
                with open(digest_state_path, "r") as f:
                    digest_state = json.load(f)
        except Exception as e:
            print(f"[Myntra] [{account.upper()}] Could not read digest state (will create new): {e}")

        all_hits = [r for r in results if r.get("hit_target")]
        send_digest = False
        digest_title = ""

        in_midnight_window = datetime_time(0, 1) <= now_time <= datetime_time(2, 0)
        in_morning_window = datetime_time(7, 0) <= now_time <= datetime_time(9, 0)
        
        if in_midnight_window and digest_state.get("last_1201am") != today_str:
            send_digest = True
            digest_title = "Midnight (12:01 AM) Digest"
            digest_state["last_1201am"] = today_str
        elif in_morning_window and digest_state.get("last_0700am") != today_str:
            send_digest = True
            digest_title = "Morning (7:00 AM) Digest"
            digest_state["last_0700am"] = today_str
        else:
            print(f"[Myntra] [{account.upper()}] Digest check: not in a digest window, skipping (current IST time: {now_time.strftime('%H:%M')})")

        if send_digest and all_hits:
            print(f"[Myntra] [{account.upper()}] Sending {digest_title} for {len(all_hits)} active targets...")
            lines = [f"📅 <b>Myntra Price Tracker — {digest_title} [{account.upper()}]</b>\n"]
            lines.append(f"<i>Active Deals Below Desired Price:</i>\n")
            for r in all_hits:
                bp = r["best_price"] if r["best_price"] is not None else r["pdp_price"]
                lines.append(
                    f'✅ <a href="{r["url"]}">{r["name"]}</a>\n'
                    f'   Price: ₹{bp} (Target: ₹{r["target_price"]})'
                )
            await send_telegram_alert("\n\n".join(lines), platform=f"{account.upper()}MYNTRA")
            
            # Save digest state
            try:
                with open(digest_state_path, "w") as f:
                    json.dump(digest_state, f, indent=4)
            except Exception as e:
                print(f"[Myntra] [{account.upper()}] ⚠️ Failed to save digest state: {e}")

        print(f"\n[Myntra] [{account.upper()}] Done — scanned {len(results)} products, {len(all_hits)} total hit target")
        return {"scanned_products": results}

    # Execute inside our fully managed and pre-authenticated async browser manager!
    return await run_with_playwright(myntra_flow, platform="myntra", cookie_type=account)


async def track_prices():
    """
    Main entry point for Myntra scraping.
    Runs dual-account tracking sequentially: 'new' account then 'old' account.
    """
    print(f"\n[Myntra] === Starting dual-account price checking ===")
    
    # ─── New Account Scan ───
    print(f"\n[Myntra] --- Launching scraper for NEW account ---")
    try:
        res_new = await myntra_price_check_raw("new")
        print(f"[Myntra] NEW account cycle complete. Result keys: {list(res_new.keys())}")
    except Exception as e:
        print(f"[Myntra] ERROR running NEW account cycle: {e}")
        
    # Wait briefly between accounts
    await asyncio.sleep(5)
    
    # ─── Old Account Scan ───
    print(f"\n[Myntra] --- Launching scraper for OLD account ---")
    try:
        res_old = await myntra_price_check_raw("old")
        print(f"[Myntra] OLD account cycle complete. Result keys: {list(res_old.keys())}")
    except Exception as e:
        print(f"[Myntra] ERROR running OLD account cycle: {e}")

    print("\n[Myntra] === Dual-account price checking complete ===")


def track_prices_sync():
    """Synchronous wrapper for CLI compatibility."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(track_prices())
    finally:
        loop.close()


if __name__ == "__main__":
    track_prices_sync()
