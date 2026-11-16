"""Debug: what does headless Chromium actually receive from Indeed?"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL = "https://uk.indeed.com/jobs?q=investment%20analyst&l=London"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

with sync_playwright() as pw:
    for headless in (True, False):
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=UA, locale="en-GB", viewport={"width": 1280, "height": 900})
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        page = context.new_page()
        response = page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        print(f"headless={headless} status={response.status if response else '?'} cards={len(page.query_selector_all('[data-testid=\"slider_item\"]'))}")
        print(f"  title: {page.title()[:90]}")
        print(f"  body[{len(body)}]: {body[:220].replace(chr(10), ' | ')}")
        context.close()
        browser.close()
