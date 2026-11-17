"""What does the education section offer, and what wording does it use?

Read-only: types into the search boxes to reveal their option lists, chooses
nothing, and never presses Next or Submit.
"""

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


def show_fields(page):
    rows = page.evaluate(
        """
        () => Array.from(document.querySelectorAll("[id^='education-']"))
          .filter(el => el.tagName === "INPUT" || el.tagName === "BUTTON")
          .map(el => ({
            id: el.id,
            tag: el.tagName.toLowerCase(),
            type: el.type || "",
            value: (el.value || "").slice(0, 24),
            label: (el.getAttribute("aria-label") || "").slice(0, 60),
          }))
        """
    )
    for row in rows:
        print(f"  {row['tag']:<7} {row['id']:<50} type={row['type']:<8} value={row['value']!r} {row['label']}")


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
    page.wait_for_timeout(1000)
    print("step:", workday.step(page), page.url)

    if workday.step(page) == "my-information":
        next_button = page.query_selector(workday.NEXT)
        if next_button is not None:
            fill._click(page, next_button)
            page.wait_for_timeout(6000)
        print("after Next:", workday.step(page), page.url)

    print("\n=== education fields present ===")
    show_fields(page)

    searches = page.query_selector_all("input[placeholder='Search']")
    print(f"\n=== {len(searches)} type-ahead fields ===")
    for index, search in enumerate(searches[:3]):
        described = search.get_attribute("aria-describedby") or ""
        print(f"\n-- field {index} (aria-describedby={described}) --")
        fill._click(page, search)
        page.wait_for_timeout(400)
        search.type("Exet", delay=90)
        page.wait_for_timeout(2500)
        options = page.evaluate(
            """
            () => Array.from(document.querySelectorAll("[role='option']"))
              .filter(el => el.getBoundingClientRect().height > 0)
              .map(el => (el.innerText || "").trim())
              .filter(Boolean)
              .slice(0, 10)
            """
        )
        print("   typing 'Exet' offered:", options)
        page.keyboard.press("Escape")

    # Degree is usually a selectinput: open it and list its options.
    print("\n=== dropdown buttons on this step ===")
    for button in workday._dropdown_buttons(page):
        print("  ", button.get_attribute("id"), "|", button.get_attribute("aria-label"))

    page.screenshot(path="output/workday-education.png", full_page=True)
    Path("output/workday-education.html").write_text(page.content(), encoding="utf-8")
    print("\nsaved output/workday-education.png and .html")
    browser.close()
