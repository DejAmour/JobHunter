"""Debug: why did the adapter extract nothing when the page has cards?"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from jobhunter.sources import IndeedBrowser  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

URL = "https://uk.indeed.com/jobs?q=investment%20analyst&l=London"

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(user_agent=IndeedBrowser.USER_AGENT, locale="en-GB")
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
    page = context.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    cards = page.query_selector_all(IndeedBrowser.CARD)
    print(f"cards: {len(cards)}")
    for card in cards[:4]:
        try:
            data = card.evaluate(IndeedBrowser.EXTRACT)
        except Exception as error:  # noqa: BLE001
            data = f"ERROR {type(error).__name__}: {error}"
        print(" ", data)
    context.close()
    browser.close()
