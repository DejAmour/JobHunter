"""The Workday markup as it actually is, not as the documentation says.

Every selector here was read off the live M&G tenant: the cookie banner that
covers the Apply button, the Apply shelf with three routes, step 1 of 6 being
Create Account, "Sign In" being a toggle on that same page, and the one-pixel
bot trap named "website" that flags the application if anything fills it.
"""

from __future__ import annotations

import pytest

from jobhunter import fill

playwright = pytest.importorskip("playwright.sync_api")


PAGE = """
<!doctype html><html><body>
  <button data-automation-id="legalNoticeAcceptButton">Accept Cookies</button>
  <a data-automation-id="adventureButton" role="button">Apply</a>

  <div id="shelf" hidden>
    <a data-automation-id="applyManually">Apply Manually</a>
    <a data-automation-id="autofillWithResume">Autofill with Resume</a>
  </div>

  <div id="create" hidden>
    <form data-automation-id="signInFormo">
      <input data-automation-id="email" type="text">
      <input data-automation-id="password" type="password">
      <input data-automation-id="verifyPassword" type="password">
      <button data-automation-id="signInLink" type="button">Sign In</button>
      <button data-automation-id="createAccountSubmitButton" type="button">Create Account</button>
    </form>
  </div>

  <div id="signin" hidden>
    <form data-automation-id="signInFormo">
      <input data-automation-id="email" type="text">
      <input data-automation-id="password" type="password">
      <button data-automation-id="createAccountLink" type="button">Create Account</button>
      <button data-automation-id="signInSubmitButton" type="button">Sign In</button>
    </form>
  </div>

  <!-- the bot trap: one pixel tall, labelled, and never to be filled -->
  <input data-automation-id="beecatcher" type="text" name="website"
         aria-label="Enter website. This input is for robots only, do not enter if you are human."
         style="height:1px;width:1px">
</body>
<script>
  const honey = () => document.querySelector("[data-automation-id=beecatcher]").value;
  const flagged = () => location.href = "flagged.html";

  document.querySelector("[data-automation-id=adventureButton]")
    .addEventListener("click", () => document.getElementById("shelf").hidden = false);
  document.querySelector("[data-automation-id=applyManually]")
    .addEventListener("click", () => {
      document.getElementById("shelf").hidden = true;
      document.getElementById("create").hidden = false;
    });
  document.querySelector("[data-automation-id=signInLink]")
    .addEventListener("click", () => {
      document.getElementById("create").hidden = true;
      document.getElementById("signin").hidden = false;
    });
  document.querySelector("[data-automation-id=createAccountLink]")
    .addEventListener("click", () => {
      document.getElementById("signin").hidden = true;
      document.getElementById("create").hidden = false;
    });
  document.querySelector("[data-automation-id=createAccountSubmitButton]")
    .addEventListener("click", () => honey() ? flagged() : location.href = "code.html");
  document.querySelector("[data-automation-id=signInSubmitButton]")
    .addEventListener("click", () => honey() ? flagged() : location.href = "myinfo.html");
</script>
</html>
"""

CODE = """
<!doctype html><html><body>
  <input data-automation-id="verificationCode">
  <button data-automation-id="verifyButton" type="button">Verify</button>
  <script>
    document.querySelector("[data-automation-id=verifyButton]")
      .addEventListener("click", () => location.href = "myinfo.html");
  </script>
</body></html>
"""

MY_INFO = """
<!doctype html><html><body>
  <input id="firstName" data-automation-id="legalNameSection_firstName">
  <input id="lastName" data-automation-id="legalNameSection_lastName">
</body></html>
"""

FLAGGED = "<!doctype html><html><body><h1>Application flagged</h1></body></html>"

# The form the M&G tenant actually renders: ids joined with "--", a fieldset on
# the same page, and no data-automation-id on the inputs at all.
MY_INFO_NEW = """
<!doctype html><html><body>
  <fieldset>
    <legend>Are you a current or former worker?</legend>
    <input type="radio" name="candidateIsPreviousWorker" value="true">
    <input type="radio" name="candidateIsPreviousWorker" value="false">
  </fieldset>
  <input id="name--legalName--firstName" name="legalName--firstName" aria-required="true">
  <input id="name--legalName--lastName" name="legalName--lastName" aria-required="true">
  <input id="phoneNumber--phoneNumber" name="phoneNumber" aria-required="true" aria-invalid="true">
  <input id="address--addressLine1" name="addressLine1">
  <input id="address--city" name="city">
  <input id="address--postalCode" name="postalCode">
  <button data-automation-id="pageFooterNextButton" type="button">Next</button>
</body></html>
"""


@pytest.fixture()
def site(tmp_path):
    (tmp_path / "apply.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "code.html").write_text(CODE, encoding="utf-8")
    (tmp_path / "myinfo.html").write_text(MY_INFO, encoding="utf-8")
    (tmp_path / "flagged.html").write_text(FLAGGED, encoding="utf-8")
    return tmp_path


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


def test_prepare_accepts_cookies_then_walks_apply_and_apply_manually(site, page):
    page.goto((site / "apply.html").as_uri(), wait_until="domcontentloaded")
    step = fill.Workday().prepare(page, timeout_ms=10_000)
    assert step == "create-account"
    assert page.query_selector("[data-automation-id='createAccountSubmitButton']").is_visible()


def test_sign_in_toggles_off_the_create_form(site, page):
    page.goto((site / "apply.html").as_uri(), wait_until="domcontentloaded")
    workday = fill.Workday()
    workday.prepare(page, timeout_ms=10_000)
    status = workday.sign_in(page, email="candidate@example.com", password="hunter2")
    assert status == "signed-in"
    assert page.url.endswith("myinfo.html")


def test_creating_the_account_asks_for_the_code(site, page):
    page.goto((site / "apply.html").as_uri(), wait_until="domcontentloaded")
    workday = fill.Workday()
    workday.prepare(page, timeout_ms=10_000)
    status = workday.sign_in(page, email="candidate@example.com", password="hunter2", create=True)
    assert status == "code-needed"
    assert page.url.endswith("code.html")


def test_the_code_is_entered_and_the_page_moves_on(site, page):
    page.goto((site / "code.html").as_uri(), wait_until="domcontentloaded")
    workday = fill.Workday()
    # The code page has no account form, so exercise the code field directly.
    assert workday._code_field(page) is not None
    fill._type(workday._code_field(page), "K7Q2P9")
    page.click("[data-automation-id='verifyButton']")
    page.wait_for_timeout(500)
    assert page.url.endswith("myinfo.html")


def test_the_bot_trap_is_never_filled(site, page):
    """The fixture redirects to a flag page if the trap has a value in it."""
    page.goto((site / "apply.html").as_uri(), wait_until="domcontentloaded")
    workday = fill.Workday()
    workday.prepare(page, timeout_ms=10_000)
    status = workday.sign_in(page, email="candidate@example.com", password="hunter2")
    assert status == "signed-in"
    assert not page.url.endswith("flagged.html")


OVERLAY = """
<!doctype html><html><body>
  <style>
    #slot { position: relative; width: 220px; height: 40px; }
    #slot button { width: 220px; height: 40px; }
    #slot .surface { position: absolute; inset: 0; z-index: 5; }
  </style>
  <form data-automation-id="signInFormo">
    <input data-automation-id="email" type="text">
    <input data-automation-id="password" type="password">
    <div id="slot">
      <!-- The live shape: the real button is a decoy. aria-hidden, out of tab
           order, and with no handler of its own. -->
      <button type="submit" data-automation-id="signInSubmitButton"
              aria-hidden="true" tabindex="-2">Sign In</button>
      <!-- The visible surface, which is what a person actually clicks. -->
      <div class="surface" data-automation-id="click_filter" role="button"
           aria-label="Sign In"></div>
    </div>
  </form>
  <script>
    document.querySelector("form").addEventListener("submit", e => e.preventDefault());
    document.querySelector("[data-automation-id=click_filter]")
      .addEventListener("click", () => location.href = "myinfo.html");
  </script>
</body></html>
"""


def test_the_click_filter_is_the_button_and_must_not_be_removed(site, page):
    """Live M&G failure, exactly.

    The recorder showed zero requests, no error and no console message. The DOM
    explained it: the submit button is `aria-hidden` and out of tab order, and a
    `click_filter` div in front of it owns the handler. An earlier version
    removed that div as an "overlay" and then clicked the decoy, which submits
    nothing. Clicking at the element's coordinates lets the real surface take it.
    """
    (site / "overlay.html").write_text(OVERLAY, encoding="utf-8")
    page.goto((site / "overlay.html").as_uri(), wait_until="domcontentloaded")
    status = fill.Workday().sign_in(page, email="candidate@example.com", password="hunter2")
    assert status == "signed-in"
    assert page.url.endswith("myinfo.html")


DEAD_CLICK = """
<!doctype html><html><body>
  <input data-automation-id="email" type="text">
  <input data-automation-id="password" type="password">
  <!-- a click here is swallowed: reported as delivered, does nothing -->
  <button data-automation-id="signInSubmitButton" type="button">Sign In</button>
  <script>
    const password = document.querySelector("[data-automation-id=password]");
    password.addEventListener("keydown", function (event) {
      if (event.key === "Enter") location.href = "myinfo.html";
    });
  </script>
</body></html>
"""


def test_a_swallowed_submit_click_falls_back_to_enter(site, page):
    """Live M&G failure: the click landed, the form did not move, no error shown."""
    (site / "dead.html").write_text(DEAD_CLICK, encoding="utf-8")
    page.goto((site / "dead.html").as_uri(), wait_until="domcontentloaded")
    status = fill.Workday().sign_in(page, email="candidate@example.com", password="hunter2")
    assert status == "signed-in"
    assert page.url.endswith("myinfo.html")


CONTROLLED = """
<!doctype html><html><body>
  <!-- A React-ish controlled input: it discards anything that did not arrive as
       a keystroke, which is how a form ends up looking filled and submitting
       nothing. -->
  <input id="stubborn" data-automation-id="email">
  <script>
    const el = document.getElementById("stubborn");
    let touched = false;
    el.addEventListener("keydown", () => { touched = true; });
    const discard = () => { if (!touched && el.value !== "") el.value = ""; };
    el.addEventListener("input", discard);
    el.addEventListener("change", discard);
  </script>
</body></html>
"""


def test_typing_is_verified_and_falls_back_to_keystrokes(site, page):
    (site / "controlled.html").write_text(CONTROLLED, encoding="utf-8")
    page.goto((site / "controlled.html").as_uri(), wait_until="domcontentloaded")
    field = page.query_selector("#stubborn")
    fill._type(field, "candidate@example.com")
    assert field.input_value() == "candidate@example.com"


def test_the_wizard_runs_the_whole_way_to_my_information(site, page):
    page.goto((site / "apply.html").as_uri(), wait_until="domcontentloaded")
    report = fill.Workday().fill(
        page,
        fill.Values(first_name="Jed", last_name="Dahlke"),
        None,
        advance=True,
        email="candidate@example.com",
        password="hunter2",
    )
    assert report.error == ""
    assert report.sign_in == "signed-in"
    # Step 1 of 6 is Create Account, with Sign In behind a toggle on the same page.
    assert report.steps[0] == "create-account"
    assert "my-information" in report.steps
    assert {"first_name", "last_name"} <= set(report.filled)


def test_a_fieldset_elsewhere_does_not_make_my_information_look_like_questions(site, page):
    """Live M&G failure: the generic "fieldset legend" marker matched step 1."""
    (site / "myinfo-new.html").write_text(MY_INFO_NEW, encoding="utf-8")
    page.goto((site / "myinfo-new.html").as_uri(), wait_until="domcontentloaded")
    assert fill.Workday().step(page) == "my-information"


def test_the_newer_workday_ids_are_filled(site, page):
    (site / "myinfo-new.html").write_text(MY_INFO_NEW, encoding="utf-8")
    page.goto((site / "myinfo-new.html").as_uri(), wait_until="domcontentloaded")
    report = fill.Workday().fill(
        page,
        fill.Values(
            first_name="Jed",
            last_name="Dahlke",
            phone="07393434084",
            address="27 Shareford Way",
            city="Exeter",
            postcode="EX5 7EZ",
        ),
        None,
        advance=False,
    )
    assert {"first_name", "last_name", "phone", "address", "city", "postcode"} <= set(
        report.filled
    )
    assert page.input_value("#name--legalName--firstName") == "Jed"


def test_it_stops_and_names_the_invalid_field_instead_of_clicking_next_forever(site, page):
    """Live M&G failure: six Next clicks into the same validation wall, no error."""
    (site / "myinfo-new.html").write_text(MY_INFO_NEW, encoding="utf-8")
    page.goto((site / "myinfo-new.html").as_uri(), wait_until="domcontentloaded")
    report = fill.Workday().fill(
        page,
        fill.Values(first_name="Jed", last_name="Dahlke"),  # no phone
        None,
        advance=True,
    )
    assert "will not advance" in report.error
    assert "phoneNumber" in report.error
    assert len(report.steps) <= 3
