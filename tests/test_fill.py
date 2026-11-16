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
