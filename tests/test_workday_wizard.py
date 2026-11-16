"""Walking a Workday-shaped wizard.

The vendored Workday fixtures are single pages, so the step-by-step filling is
tested there. This file adds a two-step chain of its own to prove the advance
loop actually moves between pages and stops at the review step.
"""

from __future__ import annotations

import pytest

from jobhunter import fill

playwright = pytest.importorskip("playwright.sync_api")

STEP_ONE = """
<!doctype html><html><body><main data-step="my-information">
<form>
  <label for="firstName">First Name *</label>
  <input id="firstName" required>
  <label for="email">Email *</label>
  <input id="email" type="email" required>
  <button type="button" data-automation-id="pageFooterNextButton"
          onclick="location.href='step2.html'">Save and Continue</button>
</form>
</main></body></html>
"""

STEP_TWO = """
<!doctype html><html><body><main data-step="review">
<h1>Review</h1>
<button data-automation-id="submitButton">Submit</button>
</main></body></html>
"""


@pytest.fixture()
def page():
    from playwright.sync_api import sync_playwright

    runtime = sync_playwright().start()
    browser = runtime.chromium.launch(headless=True)
    tab = browser.new_page()
    try:
        yield tab
    finally:
        browser.close()
        runtime.stop()


def test_wizard_advances_and_stops_at_review(tmp_path, page):
    (tmp_path / "step1.html").write_text(STEP_ONE, encoding="utf-8")
    (tmp_path / "step2.html").write_text(STEP_TWO, encoding="utf-8")
    page.goto((tmp_path / "step1.html").as_uri(), wait_until="domcontentloaded")

    values = fill.Values(first_name="Jed", email="jeddahlke@example.com")
    report = fill.Workday().fill(page, values, None, advance=True, steps=4)

    assert report.error == ""
    assert report.steps[0] == "my-information"
    assert report.steps[-1] == "review"
    assert "first_name" in report.filled and "email" in report.filled


def test_without_advance_it_stays_on_the_first_step(tmp_path, page):
    (tmp_path / "step1.html").write_text(STEP_ONE, encoding="utf-8")
    page.goto((tmp_path / "step1.html").as_uri(), wait_until="domcontentloaded")

    report = fill.Workday().fill(page, fill.Values(first_name="Jed"), None, advance=False)

    assert report.steps == ["my-information"]
    assert page.url.endswith("step1.html")
