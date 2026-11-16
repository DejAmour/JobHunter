"""The question engine: facts transcribed, words reviewed.

The distinction is the whole point. "Do you require sponsorship?" on a UK role has
one right answer and is safe to fill. "Why this role?" is writing, so it comes
from a paragraph you wrote, and is flagged for a read even when it is filled.
"""

from __future__ import annotations

import pytest

from jobhunter import fill, questions


def ctx(**overrides) -> questions.Context:
    answers = {
        "notice_period": "Available immediately",
        "highest_qualification": "MSc Mathematical Finance (Distinction)",
        "professional_qualifications": "CISI IAD; CFA IMC",
        "how_did_you_hear": "Company website",
        "linkedin": "",
        "free_text": {
            "why_role": "I want {role} at {company} because of {market}.",
            "strengths": "Three years of fund research.",
        },
    }
    answers.update(overrides.pop("answers", {}))
    return questions.Context(
        market=overrides.pop("market", "uk"),
        company=overrides.pop("company", "Man Group"),
        role=overrides.pop("role", "Credit Analyst"),
        answers=answers,
        candidate={"years_experience": 3.0, "needs_sponsorship": False},
    )


@pytest.mark.parametrize(
    "label",
    [
        "Will you now or in the future require sponsorship?",
        "Do you require visa sponsorship to work in the UK?",
        "Sponsorship required *",
    ],
)
def test_sponsorship_is_no_and_certain(label):
    answer = questions.answer_for(label, ctx())
    assert answer is not None
    assert answer.value == "No"
    assert answer.confidence == "certain"


def test_right_to_work_is_yes():
    answer = questions.answer_for("Are you legally authorized to work in the United Kingdom?", ctx())
    assert answer is not None and answer.value == "Yes"


def test_a_question_about_another_country_is_not_answered():
    # The answer bank says "no sponsorship needed" for the UK. It says nothing
    # about the United States, and answering "Yes" there would be a false
    # statement on a legal declaration.
    answer = questions.answer_for("Are you legally authorized to work in the United States?", ctx())
    assert answer is not None
    assert answer.value == ""
    assert answer.confidence == "review"
    assert "US" in answer.reason

    sponsorship = questions.answer_for("Will you require sponsorship in the US?", ctx())
    assert sponsorship is not None
    assert sponsorship.value == ""
    assert sponsorship.confidence == "review"


def test_australian_market_questions_are_answered():
    context = ctx(market="au")
    answer = questions.answer_for("Do you have the right to work in Australia?", context)
    assert answer is not None and answer.value == "Yes"
    # ...but the UK is a different market, so it is left alone.
    uk = questions.answer_for("Are you authorised to work in the UK?", context)
    assert uk is not None and uk.confidence == "review"


def test_sponsorship_flips_when_the_bank_says_so():
    candidate = {"years_experience": 3.0, "needs_sponsorship": True}
    context = ctx()
    context.candidate = candidate
    answer = questions.answer_for("Do you require sponsorship?", context)
    assert answer is not None
    assert answer.confidence == "review"


def test_facts_come_from_the_bank():
    assert questions.answer_for("What is your notice period?", ctx()).value == "Available immediately"
    assert questions.answer_for("How did you hear about us?", ctx()).value == "Company website"
    assert questions.answer_for("Highest level of education", ctx()).value.startswith("MSc")


def test_missing_fact_is_reported_not_invented():
    answer = questions.answer_for("What are your salary expectations?", ctx())
    assert answer is not None
    assert answer.value == ""
    assert answer.confidence == "review"


def test_protected_questions_are_never_answered():
    for label in ("What is your gender?", "Ethnicity", "Veteran status", "Do you have a disability?"):
        answer = questions.answer_for(label, ctx())
        assert answer is not None
        assert answer.value == ""
        assert "protected" in answer.reason


def test_free_text_uses_the_written_paragraph():
    answer = questions.answer_for("Why do you want this role?", ctx())
    assert answer is not None
    assert answer.value == "I want Credit Analyst at Man Group because of the UK."
    # Filled, but flagged: a paragraph you wrote still deserves a read.
    assert answer.confidence == "review"


def test_free_text_with_no_paragraph_is_left_alone():
    answer = questions.answer_for("Why do you want to work for us?", ctx())
    assert answer is not None
    assert answer.value == ""
    assert answer.confidence == "review"


def test_unknown_question_returns_nothing():
    assert questions.answer_for("Favourite colour", ctx()) is None


def test_yes_no_group_gets_the_matching_option(tmp_path):
    """The Workday-shaped fixture has two radio groups and one select."""
    url = (
        tmp_path
        / "questions.html"
    )
    source = (
        fill.Path(__file__).resolve().parents[1]
        / "vendor"
        / "jobappagent"
        / "fixtures"
        / "workday"
        / "application-questions.html"
    )
    pytest.importorskip("playwright.sync_api")
    report = fill.run(
        source.as_uri(),
        fill.Values(),
        ats="questions",
        ctx=ctx(),
        headless=True,
    )
    assert report.error == ""
    answered = dict(report.questions_answered)
    # The fixture asks about US work authorisation. This candidate is applying in
    # the UK, so that question is left alone rather than answered "Yes".
    assert not any("United States" in question for question in answered)
    assert any("United States" in question for question, _ in report.questions_review)
    assert answered.get("Will you now or in the future require employer sponsorship? *") == "No"
