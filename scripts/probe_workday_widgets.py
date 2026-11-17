"""Open the real My Information page and dump the two controls that resist.

Read-only: it opens dropdowns, never chooses anything, and never presses Next
or Submit. The session file means no password is needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(r"C:\Users\jedda\dev\jobhunter\src")))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jobhunter import accounts, fill  # noqa: E402

URL = (
    "https://mgpru.wd3.myworkdayjobs.com/en-US/mandgprudential/job/London/"
    "Risk---Compliance-Graduate--London-_R20815"
)

home = Path.home() / ".jobhunter"
account = accounts.find(home, "M&G")
state = accounts.session_path(home, account) if account else Path("missing")

DUMP = """
() => {
  const visible = (el) => {
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  const out = [];
  const sels = [
    "[role='listbox']",
    "[role='option']",
    "[data-automation-id*='ption']",
    "[data-automation-id*='rompt']",
    "[role='dialog']",
    "ul[data-uxi-widget-type]",
  ];
  const seen = new Set();
  for (const sel of sels) {
    for (const el of document.querySelectorAll(sel)) {
      if (!visible(el)) continue;
      const html = el.outerHTML.replace(/\\s+/g, " ").slice(0, 260);
      if (seen.has(html)) continue;
      seen.add(html);
      out.push(sel + "  ==>  " + html);
    }
  }
  return out.slice(0, 14);
}
"""

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
    print("url :", page.url)

    print("\n=== dropdown buttons found ===")
    for button in workday._dropdown_buttons(page):
        print(
            "  id=", button.get_attribute("id"),
            "| label=", button.get_attribute("aria-label"),
            "| value=", repr(button.get_attribute("value")),
        )

    for target in ("#source--source", "#name--legalName--title"):
        button = page.query_selector(target)
        print(f"\n=== opening {target} ===")
        if button is None or not button.is_visible():
            print("  not visible")
            continue
        button.click()
        page.wait_for_timeout(1200)
        for line in page.evaluate(DUMP):
            print("  " + line)
        # Is there a search box inside the popup?
        search = page.evaluate(
            """
            () => Array.from(document.querySelectorAll("input"))
              .filter(el => el.getBoundingClientRect().width > 0)
              .filter(el => !el.id.includes("--"))
              .map(el => el.outerHTML.slice(0, 200))
            """
        )
        print("  visible non-field inputs:", search[:4])
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

    print("\n=== radio group: candidateIsPreviousWorker ===")
    radios = page.query_selector_all("input[type=radio][name='candidateIsPreviousWorker']")
    for radio in radios:
        print(
            "  ",
            radio.evaluate(
                """
                (el) => {
                  const box = el.getBoundingClientRect();
                  const style = getComputedStyle(el);
                  const parent = el.parentElement;
                  return {
                    value: el.value,
                    box: [Math.round(box.x), Math.round(box.y), Math.round(box.width), Math.round(box.height)],
                    opacity: style.opacity,
                    position: style.position,
                    parent: parent ? parent.outerHTML.replace(/\\s+/g, " ").slice(0, 420) : "",
                  };
                }
                """
            ),
        )

    print("\n  elements with role=radio or aria-checked:")
    for line in page.evaluate(
        """
        () => Array.from(document.querySelectorAll("[role=radio], [aria-checked]"))
          .filter(el => el.getBoundingClientRect().width > 0)
          .map(el => el.tagName + " " + el.outerHTML.replace(/\\s+/g, " ").slice(0, 300))
          .slice(0, 8)
        """
    ):
        print("   " + line)

    Path("output/workday-myinfo-probe.html").write_text(page.content(), encoding="utf-8")
    print("\nsaved output/workday-myinfo-probe.html")
    browser.close()
