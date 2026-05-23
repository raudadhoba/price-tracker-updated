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
    # Dynamic account-specific products file path
    products_path = os.path.join(DATA_DIR, f"myntra_{account}_products.json")
    
    # Auto-initialize from legacy myntra_products.json if it exists and account-specific file is missing
    if not os.path.exists(products_path):
        legacy_path = os.path.join(DATA_DIR, "myntra_products.json")
        if os.path.exists(legacy_path):
            import shutil
            try:
                shutil.copy(legacy_path, products_path)
                print(f"[Myntra] [{account.upper()}] Automatically initialized {os.path.basename(products_path)} from legacy products file.")
            except Exception as copy_err:
                print(f"[Myntra] [{account.upper()}] ⚠️ Failed to copy legacy myntra_products.json: {copy_err}")

    # Load products from products file
    products = []
    try:
        if os.path.exists(products_path):
            with open(products_path, "r", encoding="utf-8") as f:
                products = json.load(f)
            if not isinstance(products, list):
                products = []
            
            # Ensure every product has standard keys, migrate legacy prefixed ones, and clean up
            for p in products:
                if "id" not in p:
                    p["id"] = str(uuid.uuid4())
                if "prices" in p:
                    del p["prices"]
                
                # Migrate prefixed alert keys to standard keys if standard keys are missing or 0.0/falsy
                for key in ["last_notified_price", "last_notified_date"]:
                    pref_key = f"{account}_{key}"
                    if pref_key in p:
                        val = p.get(key)
                        if val is None or val == 0.0 or val == "0.0" or val == "":
                            p[key] = p[pref_key]
                
                # Pop all legacy prefixed keys and unnecessary keys to keep database completely clean
                unnecessary_keys = [
                    "last_checked_price", "lowest_notified_price", "last_scanned_price",
                    "new_last_checked_price", "new_lowest_notified_price", "new_last_scanned_price",
                    "old_last_checked_price", "old_lowest_notified_price", "old_last_scanned_price"
                ]
                for key in unnecessary_keys:
                    p.pop(key, None)
                for prefix in ["new_", "old_"]:
                    for key in ["pdp_price", "best_price", "mrp", "last_scanned_price", "last_checked_price", "scan_status", "last_scanned_at", "last_notified_price", "last_notified_date", "lowest_notified_price"]:
                        p.pop(f"{prefix}{key}", None)
                
    except Exception as e:
        print(f"[Myntra] [{account.upper()}] ERROR reading products from {products_path}: {e}")
        return {"error": f"Error reading products: {e}"}

    print(f"[Myntra] [{account.upper()}] Found {len(products)} tracked products")

    if not products:
        print(f"[Myntra] [{account.upper()}] No products to scan")
        return {"message": "No Myntra products tracked"}

    async def myntra_flow(context, page_unused):
        # We can close the default page since we will create concurrent pages
        try:
            await page_unused.close()
        except:
            pass

        results = []
        scan_time = datetime.now(timezone.utc).isoformat()
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        now_dt = datetime.now(ist_tz)
        today_str = now_dt.strftime("%Y-%m-%d")
        now_time = now_dt.time()

        in_midnight_window = datetime_time(0, 1) <= now_time <= datetime_time(2, 0)
        in_morning_window = datetime_time(7, 0) <= now_time <= datetime_time(9, 0)

        # Concurrency limit (default 1 parallel browser page for sequential run, configurable via env)
        concurrency = int(os.getenv("MYNTRA_CONCURRENCY", "1"))
        sem = asyncio.Semaphore(concurrency)

        async def scan_single_product(index, product):
            # Smoothly stagger the initial request startups outside the semaphore.
            # This distributes the resource spike of opening new tabs and launching initial requests,
            # but doesn't waste any semaphore worker slots with idle sleeping during execution.
            stagger = (index % concurrency) * 0.15
            await asyncio.sleep(stagger)

            async with sem:
                url = product.get("url")
                if not url:
                    return

                name = product.get("name", "Unknown Product")
                print(f"[Myntra] [{account.upper()}] Starting [{index+1}/{len(products)}]: {name[:45]}...")

                page = await context.new_page()
                
                # Dynamic ad-blocker, tracker-blocker and stylesheet-blocker to save immense bandwidth & speed up loads
                async def block_resources_parallel(route):
                    request = route.request
                    if request.resource_type in ["image", "media", "font", "stylesheet"]:
                        await route.abort()
                    elif any(domain in request.url for domain in [
                        "google-analytics.com", "googletagmanager.com", "criteo.com", 
                        "adsystem", "doubleclick.net", "segment.io", "branch.io", 
                        "newrelic.com", "nr-data.net", "hotjar.com", "facebook.net", 
                        "facebook.com", "googletagservices.com", "googleadservices.com", 
                        "vizury.com", "adnxs.com"
                    ]):
                        await route.abort()
                    else:
                        await route.continue_()

                await page.route("**/*", block_resources_parallel)

                try:
                    # Open product page with 12s timeout (fail fast if page hangs)
                    await page.goto(url, wait_until="domcontentloaded", timeout=12000, referer="https://www.myntra.com/")
                    
                    # Wait for price element to render in DOM (timeout of 2.5s)
                    try:
                        await page.wait_for_selector(".pdp-price", timeout=2500)
                    except:
                        pass
                    
                    # Ultra-short 150ms buffer for dynamic coupon/best-prices hydration
                    await page.wait_for_timeout(150)
                    
                    # ─ Extract PDP Price ─
                    pdp_price = None
                    pdp_el = await page.query_selector(".pdp-price")
                    if not pdp_el:
                        pdp_el = await page.query_selector(".pdp-price strong")
                    if pdp_el:
                        pdp_text = await pdp_el.inner_text()
                        pdp_price = _parse_price(pdp_text)

                    # ─ Extract MRP ─
                    mrp = None
                    mrp_el = await page.query_selector(".pdp-mrp s")
                    if not mrp_el:
                        mrp_el = await page.query_selector(".pdp-mrp")
                    if mrp_el:
                        mrp_text = await mrp_el.inner_text()
                        mrp = _parse_price(mrp_text)

                    # ─ Extract Best Price ─
                    best_price = None
                    offer_titles = await page.query_selector_all(".pdp-offers-offerTitle")
                    for title_el in offer_titles:
                        title_text = await title_el.inner_text()
                        if "Best Price" in title_text:
                            price_el = await title_el.query_selector(".pdp-offers-price")
                            if price_el:
                                price_text = await price_el.inner_text()
                                best_price = _parse_price(price_text)
                            else:
                                best_price = _parse_price(title_text)
                            break

                    compare_price = best_price if best_price is not None else pdp_price

                    # Compare and alert
                    raw_target = product.get("desired_price") or product.get("target_price")
                    try:
                        target_price = float(raw_target) if raw_target is not None else None
                    except:
                        target_price = None

                    hit_target = False
                    drop_amount = None
                    drop_pct = None
                    trigger_immediate_alert = False

                    # Load standard keys with defensive placeholder handling
                    last_notified_price = product.get("last_notified_price")
                    if last_notified_price is not None:
                        try:
                            last_notified_price = float(last_notified_price)
                            if last_notified_price <= 0:
                                last_notified_price = None
                        except:
                            last_notified_price = None

                    last_notified_date = product.get("last_notified_date")
                    if not last_notified_date or str(last_notified_date).strip() in ["0.0", "0", "None"]:
                        last_notified_date = None

                    if compare_price is not None and target_price is not None and target_price > 0:
                        drop_amount = round(target_price - compare_price, 2)
                        drop_pct = round((drop_amount / target_price) * 100, 1)

                        if compare_price <= target_price:
                            hit_target = True
                            should_notify = False
                            if last_notified_date != today_str:
                                should_notify = True
                            elif last_notified_price is not None and compare_price < last_notified_price:
                                should_notify = True

                            if should_notify:
                                product["last_notified_price"] = compare_price
                                product["last_notified_date"] = today_str

                                # Prevent flooding the user with individual alerts if they will be included in the bulk digest anyway
                                in_digest_window = in_midnight_window or in_morning_window
                                if not in_digest_window:
                                    trigger_immediate_alert = True
                                    print(f"  [Alert] 🔔 NEW target hit on [{account.upper()}]! {compare_price} vs target {target_price} for {name[:45]}")

                                    # Send individual real-time alert
                                    alert_msg = (
                                        f"🛍️ <b>Myntra Price Alert — New Lowest Price! [{account.upper()} ACCOUNT]</b>\n\n"
                                        f'🟢 <a href="{url}">{name}</a>\n'
                                        f"   🔥 Price: ₹{compare_price}\n"
                                        f"   🎯 Target: ₹{target_price} | Drop: ₹{drop_amount} ({drop_pct}%)\n"
                                    )
                                    try:
                                        await send_telegram_alert(alert_msg, platform=f"{account.upper()}MYNTRA")
                                        print(f"  [Telegram] Real-time individual alert sent successfully for '{name[:30]}'")
                                    except Exception as tg_err:
                                        print(f"  [Telegram] ⚠️ Alert failed: {tg_err}")
                                else:
                                    print(f"  [Digest] Suppressing individual alert for '{name[:30]}' as it will be included in the bulk digest.")

                    # Update product dictionary in-place
                    product["pdp_price"] = pdp_price
                    product["best_price"] = best_price
                    product["mrp"] = mrp
                    product["scan_status"] = "scanned" if compare_price is not None else "failed"
                    product["last_scanned_at"] = scan_time

                    for prefix in ["new_", "old_"]:
                        for key in ["pdp_price", "best_price", "mrp", "last_scanned_price", "last_checked_price", "scan_status", "last_scanned_at", "last_notified_price", "last_notified_date", "lowest_notified_price"]:
                            product.pop(f"{prefix}{key}", None)

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
                        "scan_status": product["scan_status"],
                    })

                except Exception as ex:
                    print(f"  [Myntra] [{account.upper()}] ⚠️ Error scanning {name[:30]}: {ex}")
                    product["scan_status"] = "failed"
                    product["last_scanned_at"] = scan_time
                finally:
                    try:
                        await page.close()
                    except:
                        pass

        # Run all product scans concurrently!
        print(f"[Myntra] [{account.upper()}] Starting concurrent scan queue with {concurrency} parallel workers...")
        tasks = [scan_single_product(idx, prod) for idx, prod in enumerate(products)]
        await asyncio.gather(*tasks)

        # Save product updates back to the account-specific products file once at the end
        try:
            with open(products_path, "w", encoding="utf-8") as f:
                json.dump(products, f, indent=4)
            print(f"[Myntra] [{account.upper()}] ✅ Updated {os.path.basename(products_path)} with latest scan data")
        except Exception as e:
            print(f"[Myntra] [{account.upper()}] ⚠️ Failed to save {os.path.basename(products_path)}: {e}")

        # 2. CHECK & SEND TWICE DAILY DIGEST
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
