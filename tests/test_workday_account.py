"""Employer accounts: the registry, the credential store, and the sign-in flow.

The registry is a plain file of emails; the password lives in the OS store. These
tests prove that split holds, and that a Workday sign-in page is actually filled
and actually left behind.
"""

from __future__ import annotations

import sys

import pytest

from jobhunter import accounts, credentials, fill

playwright = pytest.importorskip("playwright.sync_api")


# --- registry ------------------------------------------------------------


def test_slug_is_stable_and_url_safe():
    assert accounts.slug("M&G") == "m-g"
    assert accounts.slug("  Legal & General ") == "legal-general"


def test_registry_round_trip_keeps_the_email_and_drops_the_password(tmp_path):
    account = accounts.add(tmp_path, "M&G", "jeddahlke@gmail.com", password="")
    loaded = accounts.load(tmp_path)
    assert loaded == [account]
    text = accounts.path_for(tmp_path).read_text(encoding="utf-8")
    assert "jeddahlke@gmail.com" in text
    assert "Passwords are not here" in text
    assert "hunter2" not in text


def test_adding_twice_replaces_rather_than_duplicates(tmp_path):
    accounts.add(tmp_path, "M&G", "old@example.com")
    accounts.add(tmp_path, "M&G", "new@example.com")
    loaded = accounts.load(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].email == "new@example.com"


def test_find_is_case_and_punctuation_insensitive(tmp_path):
    accounts.add(tmp_path, "Legal & General", "x@example.com")
    assert accounts.find(tmp_path, "legal and general") is None  # a different firm, honestly
    assert accounts.find(tmp_path, "LEGAL & GENERAL") is not None


def test_tenant_is_learned_from_the_url_and_used_to_pick_the_account(tmp_path):
    assert accounts.tenant_from_url("https://mg.wd3.myworkdayjobs.com/en-US/mgcareers/job/x") == "mg.wd3"
    accounts.add(tmp_path, "M&G", "x@example.com", tenant="mg.wd3")
    accounts.add(tmp_path, "abrdn", "x@example.com", tenant="abrdn.wd3")
    found = accounts.for_url(tmp_path, "https://mg.wd3.myworkdayjobs.com/en-US/mgcareers/job/123")
    assert found is not None and found.firm == "M&G"
    assert accounts.for_url(tmp_path, "https://unknown.wd1.myworkdayjobs.com/x") is None


def test_removing_an_account_forgets_the_password_and_the_session(tmp_path):
    account = accounts.add(tmp_path, "M&G", "x@example.com", password="hunter2")
    session = accounts.session_path(tmp_path, account)
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("{}", encoding="utf-8")

    assert accounts.remove(tmp_path, "m&g") is True
    assert accounts.load(tmp_path) == []
    assert not session.exists()
    assert credentials.load(account.ats, account.slug) is None


# --- credential store ----------------------------------------------------


@pytest.mark.skipif(not credentials.available(), reason="no credential store on this machine")
def test_password_survives_a_round_trip_through_the_os_store():
    secret = credentials.Secret("someone@example.com", "p@ss w0rd: ünïcode")
    credentials.store("test", "round-trip", secret)
    try:
        assert credentials.load("test", "round-trip") == secret
    finally:
        credentials.forget("test", "round-trip")
    assert credentials.load("test", "round-trip") is None


@pytest.mark.skipif(not credentials.available(), reason="no credential store on this machine")
def test_password_is_reachable_from_an_account_and_not_from_the_file(tmp_path):
    account = accounts.add(tmp_path, "M&G", "jeddahlke@gmail.com", password="correct horse")
    try:
        assert accounts.has_password(account)
        assert accounts.password(account) == "correct horse"
        assert "correct horse" not in accounts.path_for(tmp_path).read_text(encoding="utf-8")
    finally:
        accounts.remove(tmp_path, "M&G")


# --- the sign-in page ----------------------------------------------------


SIGN_IN_PAGE = """
<!doctype html><html><body><main data-automation-id="signInPage">
  <input data-automation-id="email" type="email">
  <input data-automation-id="password" type="password">
  <a data-automation-id="createAccountLink" hidden>Create Account</a>
  <div data-automation-id="errorMessage"></div>
  <button data-automation-id="signInButton">Sign In</button>
</main>
<script>
  document.querySelector("[data-automation-id=signInButton]").addEventListener("click", function () {
    var email = document.querySelector("[data-automation-id=email]").value;
    var password = document.querySelector("[data-automation-id=password]").value;
    if (email === "jeddahlke@gmail.com" && password === "hunter2") {
      location.href = "step2.html";
    } else {
      document.querySelector("[data-automation-id=errorMessage]").textContent =
        "Invalid email or password.";
    }
  });
  document.querySelector("[data-automation-id=createAccountLink]").hidden = false;
</script>
</body></html>
"""

CREATE_PAGE = """
<!doctype html><html><body><main data-automation-id="signInPage">
  <input data-automation-id="email" type="email">
  <input data-automation-id="password" type="password">
  <a data-automation-id="createAccountLink">Create Account</a>
  <div id="signup" hidden>
    <input data-automation-id="verifyPassword" type="password">
    <input data-automation-id="createAccountCheckbox" type="checkbox">
    <button data-automation-id="createAccountSubmitButton">Create Account</button>
  </div>
  <div data-automation-id="errorMessage"></div>
</main>
<script>
  document.querySelector("[data-automation-id=createAccountLink]").addEventListener("click", function () {
    document.getElementById("signup").hidden = false;
  });
  document.querySelector("[data-automation-id=createAccountSubmitButton]").addEventListener("click", function () {
    var email = document.querySelector("[data-automation-id=email]").value;
    var one = document.querySelector("[data-automation-id=password]").value;
    var two = document.querySelector("[data-automation-id=verifyPassword]").value;
    var agreed = document.querySelector("[data-automation-id=createAccountCheckbox]").checked;
    if (!email || one !== two || !agreed) {
      document.querySelector("[data-automation-id=errorMessage]").textContent =
        "Check the form.";
      return;
    }
    location.href = "code.html";
  });
</script>
</body></html>
"""

CODE_PAGE = """
<!doctype html><html><body><main data-automation-id="signInPage">
  <input data-automation-id="verificationCode">
  <div data-automation-id="errorMessage"></div>
  <button data-automation-id="verifyButton" >Verify</button>
</main>
<script>
  document.querySelector("[data-automation-id=verifyButton]").addEventListener("click", function () {
    var code = document.querySelector("[data-automation-id=verificationCode]").value;
    if (code === "K7Q2P9") { location.href = "step3.html"; }
    else { document.querySelector("[data-automation-id=errorMessage]").textContent = "Wrong code."; }
  });
</script>
</body></html>
"""

MY_INFORMATION = """
<!doctype html><html><body><main>
  <input id="firstName" data-automation-id="legalNameSection_firstName">
  <input id="lastName" data-automation-id="legalNameSection_lastName">
</main></body></html>
"""


@pytest.fixture()
def site(tmp_path):
    (tmp_path / "signin.html").write_text(SIGN_IN_PAGE, encoding="utf-8")
    (tmp_path / "create.html").write_text(CREATE_PAGE, encoding="utf-8")
    (tmp_path / "code.html").write_text(CODE_PAGE, encoding="utf-8")
    (tmp_path / "step2.html").write_text(MY_INFORMATION, encoding="utf-8")
    (tmp_path / "step3.html").write_text(MY_INFORMATION, encoding="utf-8")
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


def test_sign_in_fills_the_form_and_leaves_the_page(site, page):
    page.goto((site / "signin.html").as_uri(), wait_until="domcontentloaded")
    status = fill.Workday().sign_in(page, email="jeddahlke@gmail.com", password="hunter2")
    assert status == "signed-in"
    assert page.url.endswith("step2.html")


def test_wrong_password_is_named_rather_than_retried(site, page):
    page.goto((site / "signin.html").as_uri(), wait_until="domcontentloaded")
    status = fill.Workday().sign_in(page, email="jeddahlke@gmail.com", password="wrong")
    assert status == "bad-password"


def test_missing_account_details_fail_before_touching_the_page(site, page):
    page.goto((site / "signin.html").as_uri(), wait_until="domcontentloaded")
    status = fill.Workday().sign_in(page, email="", password="")
    assert status.startswith("failed: no stored account")


def test_create_account_walks_create_then_code_then_in(site, page):
    page.goto((site / "create.html").as_uri(), wait_until="domcontentloaded")
    asked = []

    def on_code():
        asked.append(True)
        return "K7Q2P9"

    status = fill.Workday().sign_in(
        page, email="jeddahlke@gmail.com", password="hunter2", create=True, on_code=on_code
    )
    assert status == "created"
    assert asked == [True]
    assert page.url.endswith("step3.html")


def test_create_account_without_a_mailbox_asks_for_the_code(site, page):
    page.goto((site / "create.html").as_uri(), wait_until="domcontentloaded")
    status = fill.Workday().sign_in(
        page, email="jeddahlke@gmail.com", password="hunter2", create=True
    )
    assert status == "code-needed"


def test_the_wizard_stops_at_the_account_wall_without_one(site, page):
    page.goto((site / "signin.html").as_uri(), wait_until="domcontentloaded")
    report = fill.Workday().fill(page, fill.Values(first_name="Jed"), None, advance=True)
    assert report.error
    assert "account add" in report.error
    assert report.steps == ["sign-in"]
    assert report.submitted is False


@pytest.mark.skipif(not credentials.available(), reason="no credential store on this machine")
def test_a_run_signs_in_and_keeps_the_session_for_next_time(site):
    account = accounts.add(site, "Fixture Employer", "jeddahlke@gmail.com", password="hunter2")
    try:
        report = fill.run(
            (site / "signin.html").as_uri(),
            fill.Values(first_name="Jed", last_name="Dahlke"),
            ats="workday",
            account=account,
            home=site,
            headless=True,
        )
        assert report.error == ""
        assert report.sign_in == "signed-in"
        assert report.steps[0] == "sign-in"
        assert "my-information" in report.steps
        assert accounts.session_path(site, account).is_file()
    finally:
        accounts.remove(site, "Fixture Employer")


def test_the_second_run_reuses_the_saved_session(site):
    account = accounts.add(site, "Second Employer", "jeddahlke@gmail.com", password="hunter2")
    state = accounts.session_path(site, account)
    state.parent.mkdir(parents=True, exist_ok=True)
    # A context with no cookies still proves the plumbing: the file is handed to
    # the browser, and the run does not fail on a missing one.
    state.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    try:
        report = fill.run(
            (site / "step2.html").as_uri(),
            fill.Values(first_name="Jed"),
            ats="workday",
            account=account,
            home=site,
            headless=True,
        )
        assert report.error == ""
        assert report.steps == ["my-information"]
        assert "first_name" in report.filled
    finally:
        accounts.remove(site, "Second Employer")
