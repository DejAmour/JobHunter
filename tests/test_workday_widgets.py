"""Workday's custom dropdowns and radio groups, from the live markup.

My Information stalled on four required controls that every text field had
already passed: "How Did You Hear About Us?", "Prefix", "Phone Device Type" and
"Are you a current or former worker?". None of them is a <select> or a labelled
checkbox, so filling them meant reading what the page actually renders.
"""

from __future__ import annotations

import pytest

from jobhunter import fill, questions

playwright = pytest.importorskip("playwright.sync_api")


WIDGETS = """
<!doctype html><html><body>
  <div role="group" aria-labelledby="previousWorker-section">
    <div id="previousWorker-section">Are you a current or former worker?</div>
    <div name="candidateIsPreviousWorker" aria-required="true">
      <span id="radio-label6">Yes</span>
      <input id="r1" type="radio" name="candidateIsPreviousWorker" value="true"
             aria-labelledby="radio-label6">
      <span id="radio-label7">No</span>
      <input id="r2" type="radio" name="candidateIsPreviousWorker" value="false"
             aria-labelledby="radio-label7">
    </div>
  </div>

  <button aria-haspopup="listbox" type="button" value=""
          aria-label="How Did You Hear About Us? Select One Required"
          name="source" id="source--source">Select One</button>
  <ul id="menu" role="listbox" style="display:none">
    <li role="option">Internet</li>
    <li role="option">Company Website</li>
    <li role="option">LinkedIn</li>
  </ul>

  <button aria-haspopup="listbox" type="button" value=""
          aria-label="Prefix Select One Required"
          name="legalName--title" id="name--legalName--title">Select One</button>
  <ul id="prefix-menu" role="listbox" style="display:none">
    <li role="option">Dr</li>
    <li role="option">Mr</li>
    <li role="option">Ms</li>
  </ul>

  <!-- already answered: must be left alone -->
  <button aria-haspopup="listbox" type="button" value="29247e57dbaf46fb855b224e03170bc7"
          aria-label="Country United Kingdom Required"
          name="country" id="country--country">United Kingdom</button>

  <script>
    const open = (id, menu) => {
      document.getElementById(id).addEventListener("click", () => {
        document.getElementById(menu).style.display = "block";
      });
      document.getElementById(menu).addEventListener("click", (event) => {
        if (event.target.tagName !== "LI") return;
        document.getElementById(id).textContent = event.target.textContent;
        document.getElementById(menu).style.display = "none";
      });
    };
    open("source--source", "menu");
    open("name--legalName--title", "prefix-menu");
  </script>
</body></html>
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


@pytest.fixture()
def widgets(tmp_path, page):
    path = tmp_path / "widgets.html"
    path.write_text(WIDGETS, encoding="utf-8")
    page.goto(path.as_uri(), wait_until="domcontentloaded")
    return page


def ctx(**answers) -> questions.Context:
    return questions.Context(answers=dict(answers))


def test_a_workday_dropdown_is_opened_and_an_option_chosen(widgets):
    report = fill.FillReport(url="", ats="workday")
    fill.Workday()._extras(
        widgets,
        ctx(how_did_you_hear="Company website", phone_device_type="Mobile", previous_worker="No"),
        report,
    )
    # Case differs between the answer bank and the option; it still matches.
    assert widgets.inner_text("#source--source") == "Company Website"
    assert "How Did You Hear About Us?" in report.filled


def test_a_radio_group_is_answered_from_the_bank(widgets):
    report = fill.FillReport(url="", ats="workday")
    fill.Workday()._extras(widgets, ctx(previous_worker="No"), report)
    assert widgets.is_checked("#r2") is True
    assert widgets.is_checked("#r1") is False
    assert "Are you a current or former worker?" in report.filled


def test_an_already_answered_dropdown_is_not_touched(widgets):
    """Country arrives preselected; reopening it would be a pointless risk."""
    report = fill.FillReport(url="", ats="workday")
    fill.Workday()._extras(widgets, ctx(previous_worker="No"), report)
    assert widgets.inner_text("#country--country") == "United Kingdom"


def test_a_missing_answer_is_reported_rather_than_guessed(widgets):
    report = fill.FillReport(url="", ats="workday")
    fill.Workday()._extras(widgets, ctx(), report)
    joined = " ".join(report.empty_answers)
    assert "Prefix" in joined
    assert "How Did You Hear About Us?" in joined
    # Nothing was chosen by guessing.
    assert widgets.inner_text("#source--source") == "Select One"
    assert widgets.inner_text("#name--legalName--title") == "Select One"


def test_an_option_that_does_not_exist_does_not_pick_a_near_match(widgets):
    """'Company website' is in the bank; a form offering only 'Internet' must not
    silently take 'Internet'."""
    report = fill.FillReport(url="", ats="workday")
    fill.Workday()._extras(widgets, ctx(how_did_you_hear="Employee referral"), report)
    assert widgets.inner_text("#source--source") == "Select One"
    assert any("no option for" in entry for entry in report.empty_answers)
