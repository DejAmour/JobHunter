"""The degree list, and what the year inputs actually are."""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, r"C:\Users\jedda\dev\jobhunter\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jobhunter import accounts, fill  # noqa: E402

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
    page.wait_for_timeout(1200)
    print("step:", workday.step(page))
    if workday.step(page) == "my-information":
        button = page.query_selector(workday.NEXT)
        if button is not None:
            fill._click(page, button)
            page.wait_for_timeout(6000)
    print("after Next:", workday.step(page))

    degree = page.query_selector("button[id$='--degree']")
    print("\ndegree button found:", degree is not None)
    if degree is not None:
        print("  label:", degree.get_attribute("aria-label"))
        fill._click(page, degree)
        page.wait_for_timeout(1200)
        texts = workday._all_option_texts(page)
        print("  option count:", len(texts))
        for text in texts[:20]:
            print("   ", text)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

    print("\n=== year inputs ===")
    for match in page.query_selector_all("input[id*='YearAttended']"):
        print(
            "  ",
            match.evaluate(
                """
                (el) => ({
                  id: el.id,
                  type: el.type,
                  value: el.value,
                  readOnly: el.readOnly,
                  visible: el.getBoundingClientRect().height > 0,
                  outer: el.outerHTML.slice(0, 220),
                })
                """
            ),
        )

    print("\n=== any year picker buttons next to them ===")
    for match in page.query_selector_all("[data-automation-id*='dateSection'], [aria-label*='Year']"):
        print("  " + match.evaluate("el => el.outerHTML.slice(0, 200)"))

    page.screenshot(path="output/workday-education2.png", full_page=True)
    browser.close()
