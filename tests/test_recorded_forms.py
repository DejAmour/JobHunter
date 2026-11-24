"""Workable and Ashby, replayed from the DOM they actually served.

The fixtures under ``tests/fixtures/recorded`` were captured by
``jobhunter record`` from live tenants on 2026-11-24 (see the ``.json`` next to
each). These tests are the proof that the selectors in the fillers match markup
nobody here wrote.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobhunter import fill, questions

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
RECORDED = ROOT / "tests" / "fixtures" / "recorded"
CV = ROOT / "output" / "Jed Dahlke CV.pdf"


def fixture(ats: str) -> Path:
    return next((RECORDED / ats).glob("*.html")).resolve()


def values(**overrides) -> fill.Values:
    base = dict(
        first_name="Jed",
        last_name="Dahlke",
        full_name="Jed Dahlke",
        email="jeddahlke@example.com",
        phone="07393434084",
        location="Exeter, Devon",
        country="United Kingdom",
        cv_path=str(CV),
        linkedin="",
    )
    base.update(overrides)
    return fill.Values(**base)


def context(**answers) -> questions.Context:
    return questions.Context(
        market="uk",
        company="Neudata",
        role="Research Analyst",
        answers={"notice_period": "Available immediately", **answers},
        profile={},
        candidate={},
        evidence=[],
        disclosures={},
    )


needs_cv = pytest.mark.skipif(not CV.is_file(), reason="CV not built")


@needs_cv
def test_workable_fills_the_fields_the_live_form_actually_has(tmp_path):
    report = fill.run(
        fixture("workable").as_uri(),
        values(),
        ats="workable",
        ctx=context(),
        headless=True,
        screenshot_dir=tmp_path,
    )
    assert report.error == ""
    assert {"first_name", "last_name", "email", "phone", "address", "resume"} <= set(
        report.filled
    )
    # The one-pixel city/postcode/country inputs belong to the page's type-ahead
    # and are not written to directly.
    assert "city" not in report.filled
    assert "postcode" not in report.filled
    assert report.submitted is False


@needs_cv
def test_workable_question_is_read_from_aria_labelledby(tmp_path):
    """No legend on the fieldset: the question is in ``aria-labelledby``."""
    report = fill.run(
        fixture("workable").as_uri(),
        values(),
        ats="workable",
        ctx=context(),
        headless=True,
        screenshot_dir=tmp_path,
    )
    asked = [question for question, _ in report.questions_review]
    assert any(
        "private equity fund, investment bank, or strategy consultant" in question
        for question in asked
    ), asked
    # And the page's own required-field check names the same question, not the
    # word "YES" from the first radio option.
    assert any(
        "private equity fund, investment bank, or strategy consultant" in label
        for label in report.required_left
    ), report.required_left


@needs_cv
def test_ashby_fills_system_fields_and_answers_its_questions(tmp_path):
    report = fill.run(
        fixture("ashby").as_uri(),
        values(linkedin="https://www.linkedin.com/in/jeddahlke"),
        ats="ashby",
        ctx=context(linkedin="https://www.linkedin.com/in/jeddahlke"),
        headless=True,
        screenshot_dir=tmp_path,
    )
    assert report.error == ""
    assert {"name", "email", "resume"} <= set(report.filled)
    answered = dict(report.questions_answered)
    assert answered["LinkedIn Profile"] == "https://www.linkedin.com/in/jeddahlke"
    assert answered["When will you be available to start?"] == "Available immediately"
    # A blank salary expectation is reported, not invented.
    assert any("salary" in question.lower() for question, _ in report.questions_review)


@needs_cv
def test_ashby_does_not_upload_the_cv_to_the_autofill_input(tmp_path):
    """The first file input parses the CV and rewrites fields; the CV goes to the
    resume field only."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright_api:
        browser = playwright_api.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(fixture("ashby").as_uri(), wait_until="domcontentloaded")
        report = fill.Ashby().fill(page, values(), context())
        counts = page.evaluate(
            "() => Array.from(document.querySelectorAll(\"input[type='file']\")).map((i) => i.files.length)"
        )
        browser.close()

    assert "resume" in report.filled
    # autofill, resume, cover letter: exactly one file attached, to the resume.
    assert counts.count(1) == 1, counts
    assert counts[1] == 1, counts
