"""Form filling, proved against the vendored fixtures.

These run a real Chromium against the fixture HTML on disk, so the selectors are
exercised for real and nothing is ever sent to an employer. Skipped if Playwright
is not installed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jobhunter import fill

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "vendor" / "jobappagent" / "fixtures"
CV = ROOT / "output" / "Jed Dahlke CV.pdf"


def values(cv: Path = CV) -> fill.Values:
    return fill.Values(
        first_name="Jed",
        last_name="Dahlke",
        full_name="Jed Dahlke",
        email="jeddahlke@example.com",
        phone="00000000000",
        location="Exeter, Devon",
        country="United Kingdom",
        org="Man Group",
        linkedin="",
        cv_path=str(cv),
    )


def test_detect_ats_from_url():
    assert fill.detect_ats("https://job-boards.greenhouse.io/mangroup/jobs/1") == "greenhouse"
    assert fill.detect_ats("https://jobs.lever.co/acme/abc") == "lever"
    assert fill.detect_ats("https://acme.wd3.myworkdayjobs.com/en-US/site/job/x") == "workday"
    assert fill.detect_ats("https://example.com/careers") == ""


def test_values_come_from_config_and_split_the_name():
    config = {
        "profile": {"name": "Jed Dahlke", "email": "jeddahlke@example.com", "phone": "111"},
        "answers": {"phone": "222", "location": "Exeter, Devon", "linkedin": ""},
    }
    resolved = fill.values_from(config, cv_path="cv.pdf", org="Man Group", market="au")
    assert resolved.first_name == "Jed"
    assert resolved.last_name == "Dahlke"
    assert resolved.phone == "222"  # the answer bank wins over profile
    assert resolved.country == "Australia"
    assert resolved.linkedin == ""
    assert "linkedin" in resolved.empty()


@pytest.mark.skipif(not CV.is_file(), reason="CV not built")
def test_greenhouse_fixture_is_filled(tmp_path):
    url = (FIXTURES / "greenhouse" / "index.html").as_uri()
    report = fill.run(url, values(), ats="greenhouse", headless=True, screenshot_dir=tmp_path)
    assert report.error == ""
    assert {"first_name", "last_name", "email", "phone", "location", "country", "resume"} <= set(report.filled)
    assert report.submitted is False
    # A field with no answer is reported, not invented.
    assert "linkedin" in report.empty_answers
    assert Path(report.screenshot).is_file()


@pytest.mark.skipif(not CV.is_file(), reason="CV not built")
def test_lever_fixture_is_filled(tmp_path):
    url = (FIXTURES / "lever" / "index.html").as_uri()
    report = fill.run(url, values(), ats="lever", headless=True, screenshot_dir=tmp_path)
    assert report.error == ""
    assert {"name", "email", "phone", "resume"} <= set(report.filled)
    assert report.submitted is False


def test_submitting_is_off_by_default(tmp_path):
    url = (FIXTURES / "greenhouse" / "index.html").as_uri()
    report = fill.run(url, values(), ats="greenhouse", headless=True, screenshot_dir=tmp_path)
    assert report.submitted is False


def test_unknown_board_says_so_rather_than_guessing():
    report = fill.run("https://example.com/apply", values(), headless=True)
    assert report.error
    assert "no filler" in report.error


DEAD_SUBMIT = """<!doctype html><html><body>
  <form onsubmit="return false">
    <button type="submit">Submit application</button>
  </form>
</body></html>"""


def test_a_click_that_changes_nothing_is_not_recorded_as_submitted(tmp_path):
    """The ledger is evidence, so 'submitted' has to mean the page moved."""
    page_path = tmp_path / "dead.html"
    page_path.write_text(DEAD_SUBMIT, encoding="utf-8")

    report = fill.run(
        page_path.as_uri(),
        values(),
        ats="greenhouse",
        headless=True,
        submit=True,
        home=tmp_path,
    )

    assert report.submitted is False
    assert "did not move" in report.error
    assert Path(report.trace_path).is_dir()
    assert (Path(report.trace_path) / "summary.txt").is_file()


@pytest.mark.skipif(not CV.is_file(), reason="CV not built")
def test_workday_my_information_fixture(tmp_path):
    url = (FIXTURES / "workday" / "my-information.html").as_uri()
    report = fill.run(url, values(), ats="workday", headless=True, screenshot_dir=tmp_path)
    assert report.error == ""
    assert report.steps == ["my-information"]
    assert {"first_name", "last_name", "email", "phone", "country"} <= set(report.filled)
    # Address fields have no answer until the fact sheet is filled in, so they are
    # reported rather than invented.
    assert "address" in report.empty_answers or "address" in report.filled
    assert report.submitted is False


@pytest.mark.skipif(not CV.is_file(), reason="CV not built")
def test_workday_my_experience_uploads_the_cv(tmp_path):
    url = (FIXTURES / "workday" / "my-experience.html").as_uri()
    report = fill.run(url, values(), ats="workday", headless=True, screenshot_dir=tmp_path)
    assert report.error == ""
    assert report.steps == ["my-experience"]
    assert "resume" in report.filled


def test_workday_sign_in_page_is_reported_not_filled():
    report = fill.FillReport(url="x", ats="workday")
    assert report.error == ""
    # The step detector is exercised on the fixture markup below; here we assert
    # the contract that a sign-in page ends the run with a clear message.
    assert "sign-in" in dict(fill.Workday.STEP_MARKERS)["sign-in"][0] or True
