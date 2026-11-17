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
  <!-- the live radio shape: legend holds the question, each option text is a
       sibling <label for>, and the input itself is invisible -->
  <fieldset>
    <legend><label id="radio-label1">Have you previously worked for this
      organisation? If Yes, please answer the questions below.</label></legend>
    <div name="candidateIsPreviousWorker" aria-labelledby="radio-label1" aria-required="true">
      <div class="row">
        <div class="dot">
          <input id="chit6" type="radio" name="candidateIsPreviousWorker" value="true"
                 style="opacity:0;position:absolute;width:24px;height:24px">
        </div>
        <label for="chit6">Yes</label>
      </div>
      <div class="row">
        <div class="dot">
          <input id="chit7" type="radio" name="candidateIsPreviousWorker" value="false"
                 style="opacity:0;position:absolute;width:24px;height:24px">
        </div>
        <label for="chit7">No</label>
      </div>
    </div>
  </fieldset>

  <button aria-haspopup="listbox" type="button" value=""
          aria-label="How Did You Hear About Us? Select One Required"
          name="source" id="source--source">Select One</button>
  <!-- far more options than fit: only a screenful is in the DOM until you scroll -->
  <ul id="menu" role="listbox" style="display:none;height:80px;overflow-y:auto">
  </ul>
  <style>#menu li { height: 40px; }</style>

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
    const sources = [
      "ADEM", "Black Professionals Scotland", "Bright Network",
      "Career Fair or Exhibition", "Careers in Audit", "Company Website",
      "Internet", "Job Board", "Other",
    ];
    const menu = document.getElementById("menu");
    let shown = 0;
    const grow = () => {
      if (shown >= sources.length) return;
      const stop = Math.min(shown + 4, sources.length);
      for (let i = shown; i < stop; i += 1) {
        const li = document.createElement("li");
        li.setAttribute("role", "option");
        li.textContent = sources[i];
        menu.appendChild(li);
      }
      shown = stop;
    };
    menu.addEventListener("scroll", grow);
    document.getElementById("source--source").addEventListener("click", () => {
      menu.style.display = "block";
      grow();
    });
    menu.addEventListener("click", (event) => {
      if (event.target.tagName !== "LI") return;
      document.getElementById("source--source").textContent = event.target.textContent;
      menu.style.display = "none";
    });

    const prefixMenu = document.getElementById("prefix-menu");
    document.getElementById("name--legalName--title").addEventListener("click", () => {
      prefixMenu.style.display = "block";
    });
    prefixMenu.addEventListener("click", (event) => {
      if (event.target.tagName !== "LI") return;
      document.getElementById("name--legalName--title").textContent = event.target.textContent;
      prefixMenu.style.display = "none";
    });
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
    assert widgets.is_checked("#chit7") is True
    assert widgets.is_checked("#chit6") is False
    assert any("previously worked" in entry for entry in report.filled)


def test_a_virtualised_list_is_scrolled_until_the_choice_appears(widgets):
    """"Company Website" is past the first screenful, so it must be scrolled to."""
    fill._click(widgets, widgets.query_selector("#source--source"))
    widgets.wait_for_timeout(400)
    texts = fill.Workday()._all_option_texts(widgets)
    assert "company website" in texts
    assert len(texts) >= 6


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


def test_an_inexact_match_for_another_question_is_not_taken(widgets):
    """The closeness rule is scoped to the question, not to the dropdown."""
    report = fill.FillReport(url="", ats="workday")
    fill.Workday()._extras(widgets, ctx(previous_worker="Maybe"), report)
    assert widgets.is_checked("#chit6") is False
    assert widgets.is_checked("#chit7") is False


def test_a_how_did_you_hear_question_takes_a_close_option(widgets):
    """He does not care which variant: "company website or similar"."""
    report = fill.FillReport(url="", ats="workday")
    fill.Workday()._extras(widgets, ctx(how_did_you_hear="Employee referral"), report)
    assert widgets.inner_text("#source--source") == "Company Website"
    assert any("preference" in note for note in report.notes)
