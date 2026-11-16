"""Debug: dump one Indeed card's markup so the selectors are right."""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from jobhunter.sources import IndeedBrowser  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(user_agent=IndeedBrowser.USER_AGENT, locale="en-GB")
    page = context.new_page()
    page.goto("https://uk.indeed.com/jobs?q=investment%20analyst&l=London", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    for index, card in enumerate(page.query_selector_all(IndeedBrowser.CARD)[:2]):
        print(f"--- card {index} text ---")
        print(card.inner_text()[:200].replace("\n", " | "))
        print(f"--- card {index} html ---")
        print(card.inner_html()[:1200])
    context.close()
    browser.close()
