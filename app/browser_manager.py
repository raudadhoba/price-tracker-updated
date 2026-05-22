import os
import sys
import json
import asyncio
from typing import Callable, Any
from playwright.async_api import async_playwright

# Data Paths
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(CURRENT_DIR, "data")

# ─── Browser config from environment ───────────────────────────
BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "false").strip().lower() == "true"
BROWSER_CLOSE    = os.getenv("BROWSER_CLOSE",    "true").strip().lower() == "true"


def sanitize_cookies(raw_cookies: list, fallback_domain: str) -> list:
    """Convert browser-extension exported cookies to Playwright format."""
    cleaned = []
    for c in raw_cookies:
        if not isinstance(c, dict) or "name" not in c or "value" not in c:
            continue
            
        cookie = {
            "name": str(c["name"]),
            "value": str(c["value"]),
            "domain": str(c["domain"]) if ("domain" in c and c["domain"]) else fallback_domain,
            "path": str(c.get("path", "/")),
        }
        # Translate expirationDate to expires
        if "expirationDate" in c and c["expirationDate"] is not None:
            cookie["expires"] = float(c["expirationDate"])
        elif "expires" in c and c["expires"] is not None:
            cookie["expires"] = float(c["expires"])
            
        if c.get("httpOnly"):
            cookie["httpOnly"] = True
        if c.get("secure"):
            cookie["secure"] = True
            
        # Map sameSite values robustly (case-insensitive, translate "no_restriction" to "None")
        same_site = c.get("sameSite")
        if same_site:
            same_site_str = str(same_site).strip().lower()
            if same_site_str == "no_restriction":
                cookie["sameSite"] = "None"
            elif same_site_str in ["strict", "lax", "none"]:
                cookie["sameSite"] = same_site_str.capitalize()
                
        cleaned.append(cookie)
    return cleaned


async def run_with_playwright(task_callback: Callable, platform: str, cookie_type: str | None = None) -> Any:
    """
    Launch Playwright browser with stealth & ad-blocking capabilities,
    resolves the matching platform cookies, navigates, injects them, reloads,
    and then executes the task_callback(context, page) asynchronously.
    """
    # ─── 1. Resolve Platform Configurations ───
    platform_lower = platform.lower()
    if platform_lower == "myntra":
        domain_url = "https://www.myntra.com/"
        fallback_domain = ".myntra.com"
        cookie_file = f"myntra_{cookie_type}_cookies.json" if cookie_type else "myntra_new_cookies.json"
    elif platform_lower == "amazon":
        domain_url = "https://www.amazon.in/"
        fallback_domain = ".amazon.in"
        cookie_file = "amazon_cookies.json"
    elif platform_lower == "flipkart":
        domain_url = "https://www.flipkart.com/"
        fallback_domain = ".flipkart.com"
        cookie_file = "flipkart_cookies.json"
    else:
        raise ValueError(f"Unsupported platform: {platform}")

    cookies_path = os.path.join(DATA_DIR, cookie_file)

    # ─── 2. Startup Playwright Chromium ───
    headless     = BROWSER_HEADLESS
    mode_label   = "headless" if headless else "headful"
    print(f"[Playwright] Launching Chromium ({mode_label}, incognito) for {platform.upper()}...")
    
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        channel="chromium",
        args=[
            "--incognito",
            "--disable-http2",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--lang=en-IN,en",
        ]
    )
    
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-IN",
        viewport={"width": 1366, "height": 768},
    )
    
    # Evasion scripts
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = await context.new_page()

    # Ad-blocker route filtering
    async def block_resources(route):
        request = route.request
        if request.resource_type in ["image", "media", "font"]:
            await route.abort()
        elif any(domain in request.url for domain in ["google-analytics.com", "googletagmanager.com", "criteo.com", "adsystem", "doubleclick.net"]):
            await route.abort()
        else:
            await route.continue_()

    await page.route("**/*", block_resources)

    # ─── 3. Load and Inject Cookies (Before Navigating) ───
    cookies_injected = False
    try:
        if os.path.exists(cookies_path):
            with open(cookies_path, "r") as f:
                raw_cookies = json.load(f)
            
            cookies = sanitize_cookies(raw_cookies, fallback_domain)
            if cookies:
                # Extract clean platform domain for filtering (e.g., "myntra.com")
                platform_domain = fallback_domain.lstrip(".")
                
                # Inject cookies one by one to prevent invalid or third-party cookies from crashing the process
                injected_count = 0
                for cookie in cookies:
                    try:
                        # Only inject cookies that belong to the platform domain
                        if platform_domain in cookie["domain"]:
                            await context.add_cookies([cookie])
                            injected_count += 1
                    except Exception as ce:
                        print(f"[Browser Manager] ⚠️ Error injecting cookie {cookie.get('name')}: {ce}")
                
                print(f"[Browser Manager] Successfully injected {injected_count} / {len(cookies)} cookies from: {cookie_file}")
                cookies_injected = True
            else:
                print("[Browser Manager] No valid cookies found in file. Browsing anonymously.")
        else:
            print(f"[Browser Manager] Cookie file not found at {cookies_path}. Browsing anonymously.")

        # ─── 4. Navigate to Domain ───
        print(f"[Browser Manager] Opening domain: {domain_url}...")
        await page.goto(domain_url, wait_until="domcontentloaded")
        
        # ─── 5. Check Login Status if cookies were injected ───
        if cookies_injected and platform_lower == "myntra":
            print("[Browser Manager] Checking login status on Myntra...")
            try:
                # Hover over the Profile icon to trigger active dynamic rendering of dropdown options
                profile_menu = await page.query_selector(".desktop-user")
                if profile_menu:
                    await profile_menu.hover()
                    await page.wait_for_timeout(1500)
                
                # Check for profile elements and extract email/name
                info_email_el = await page.query_selector(".desktop-infoEmail")
                info_title_el = await page.query_selector(".desktop-infoTitle")
                
                email_text = await info_email_el.inner_text() if info_email_el else None
                title_text = await info_title_el.inner_text() if info_title_el else None
                
                # Foolproof check: if "Logout" is not in the page source, the user is NOT logged in.
                # If "Logout" is present, check that the title and description don't match anonymous placeholders.
                is_logged_in = False
                html_content = await page.content()
                if "Logout" in html_content:
                    if title_text and "welcome" in title_text.lower():
                        is_logged_in = False
                    elif email_text and "access account" in email_text.lower():
                        is_logged_in = False
                    else:
                        is_logged_in = True
                
                if is_logged_in:
                    user_id = email_text.strip() if (email_text and "access account" not in email_text.lower()) else "Myntra User"
                    user_name = title_text.strip() if (title_text and "welcome" not in title_text.lower()) else "Hello"
                    print(f"[Browser Manager] 🎉 Login confirmed! {user_name} | Account ID/Mobile: {user_id}")
                    print(f"[Browser Manager] ✅ Cookies were injected properly!")
                else:
                    print("[Browser Manager] ❌ Login check failed. No logged-in profile found.")
                    if title_text or email_text:
                        print(f"[Browser Manager] (Found: '{title_text}' | '{email_text}')")
                    else:
                        print("[Browser Manager] (No profile info found)")
                    print("[Browser Manager] ⚠️ Cookies were NOT injected properly or have expired!")
            except Exception as login_err:
                print(f"[Browser Manager] ⚠️ Warning: error checking login: {login_err}")

        # ─── 6. Run Task Callback ───
        result = await task_callback(context, page)
        return result
        
    except Exception as e:
        print(f"[Browser Manager] Error in browser session: {e}")
        raise
    finally:
        if BROWSER_CLOSE:
            try:
                await browser.close()
                await pw.stop()
                print("[Browser Manager] Browser closed")
            except Exception as close_err:
                print(f"[Browser Manager] Warning: error closing browser: {close_err}")
