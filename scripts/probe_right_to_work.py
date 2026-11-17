"""What kind of control is the right-to-work status question, and what does it offer?"""

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
    page.wait_for_timeout(1000)
    for _ in range(3):
        if workday._await_step(page, seconds=20) == "questions":
            break
        button = page.query_selector(workday.NEXT)
        if button is None:
            break
        fill._click(page, button)
        page.wait_for_timeout(6000)
    print("step:", workday.step(page))

    rows = page.evaluate(
        """
        () => {
          const out = [];
          for (const el of document.querySelectorAll("button, select, input")) {
            const field = el.closest("[data-automation-id^='formField-']");
            const text = field ? (field.innerText || "") : "";
            if (!/right to work|visa status/i.test(text)) continue;
            out.push({
              tag: el.tagName.toLowerCase(),
              type: el.type || "",
              id: el.id,
              haspopup: el.getAttribute("aria-haspopup") || "",
              label: (el.getAttribute("aria-label") || "").slice(0, 90),
              outer: el.outerHTML.slice(0, 260),
            });
          }
          return out;
        }
        """
    )
    for row in rows:
        print("\n  ", row)

    for element in page.query_selector_all("button[aria-haspopup='listbox']"):
        try:
            label = element.evaluate(
                "el => { const f = el.closest(\"[data-automation-id^='formField-']\"); return f ? f.innerText : ''; }"
            )
        except Exception:  # noqa: BLE001
            continue
        if "right to work" not in label.lower() and "visa status" not in label.lower():
            continue
        print("\nopening the right-to-work dropdown")
        fill._click(page, element)
        page.wait_for_timeout(1200)
        for text in workday._all_option_texts(page):
            print("   option:", text)
        page.keyboard.press("Escape")
        break

    browser.close()
