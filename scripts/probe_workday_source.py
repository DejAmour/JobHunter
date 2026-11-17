"""What does the 'How Did You Hear About Us?' list actually contain?

Read-only: opens the dropdown, scrolls it, prints every option it found, then
closes it without choosing anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, r"C:\Users\jedda\dev\jobhunter\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jobhunter import accounts, choices, fill  # noqa: E402

URL = (
    "https://mgpru.wd3.myworkdayjobs.com/en-US/mandgprudential/job/London/"
    "Risk---Compliance-Graduate--London-_R20815"
)
home = Path.home() / ".jobhunter"
account = accounts.find(home, "M&G")
state = accounts.session_path(home, account) if account else Path("missing")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        locale="en-GB",
        viewport={"width": 1280, "height": 1400},
        storage_state=str(state) if state.is_file() else None,
    )
    page = context.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
    workday = fill.Workday()
    workday.prepare(page)
    page.wait_for_timeout(1500)
    print("step:", workday.step(page))

    button = page.query_selector("#source--source")
    if button is None:
        raise SystemExit("source dropdown not on this page")

    fill._click(page, button)
    page.wait_for_timeout(1000)

    container = workday._option_container(page)
    print("container:", container.evaluate("el => [el.tagName, el.clientHeight, el.scrollHeight]") if container else None)

    texts = workday._all_option_texts(page)
    print("option count:", len(texts))
    for text in texts:
        print("   ", text)

    print("\ninteresting:", [t for t in texts if any(word in t for word in ("company", "internet", "direct", "website", "other"))])

    picked = choices.choose("How Did You Hear About Us?", texts, "Company website")
    print("resolver would pick:", picked)

    page.keyboard.press("Escape")
    browser.close()
