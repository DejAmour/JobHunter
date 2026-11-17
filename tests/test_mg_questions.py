"""The ten knockout questions on M&G's Application Questions page, as text.

These are the real sentences from the live page, so the rules are tested against
what the form actually says rather than against a paraphrase.
"""

from __future__ import annotations

import pathlib
import tomllib

from jobhunter import questions

CONFIG = tomllib.loads(
    (pathlib.Path.home() / ".jobhunter" / "config.toml").read_text(encoding="utf-8")
)
CTX = questions.Context(
    market="uk",
    company="M&G",
    role="Risk & Compliance Graduate (London)",
    answers=dict(CONFIG.get("answers") or {}),
    candidate=dict(CONFIG.get("candidate") or {}),
)

LIVE = (
    (
        "Our 2027 Graduate programme will be commencing during September 2027 "
        "(exact date to be confirmed). Please confirm that you will be able to "
        "join us during the month of September 2027.",
        "Yes",
    ),
    (
        "Are you able to commit to working in person from the required location "
        "for the full duration of the programme? Please note that relocation "
        "expenses will not be provided.",
        "Yes",
    ),
    (
        "M&G does not offer Skilled Worker visa sponsorship for our Graduate "
        "Programme. To be eligible, you must have the right to work in the UK "
        "for the duration of the programme and beyond without requiring "
        "employer sponsorship now or in the future. Do you meet this requirement?",
        "Yes",
    ),
    (
        "Have you submitted more than one application to a 2027 Graduate or "
        "Internship programme at M&G?",
        "No",
    ),
    ("Have you previously been employed by M&G, including any internships?", "No"),
    (
        "To ensure we're aware of any potential conflicts of interest: were you "
        "referred by or are you closely connected to a Public Official, an M&G "
        "Corporate Customer, or an M&G Vendor or Supplier?",
        "No",
    ),
    ("Do you require any reasonable adjustments throughout the recruitment process?", "No"),
    (
        "To ensure you have the opportunity to demonstrate your potential in the "
        "first stage of our assessment process. Please confirm here if you would "
        "like to 'opt in'.",
        "Yes",
    ),
    (
        "To ensure we're aware of any potential conflicts of interest, please "
        "confirm whether you have worked for our auditor PwC in the last two years",
        "No",
    ),
    (
        "How did you hear about the M&G graduate/internship programme?",
        "Company website",
    ),
    (
        "Please select your current right to work / visa status",
        "UK citizen",
    ),
    (
        "M&G is a proud to be a Disability Confident Leader, and we welcome "
        "applications from candidates with long-term health conditions and "
        "disabilities. As part of this commitment, candidates who meet the "
        "minimum criteria for the role and choose to 'opt in' to the scheme when "
        "applying will be guaranteed participation in the first stage of our "
        "assessment process. Please confirm here if you would like to 'opt in'.",
        "No",
    ),
)


def test_every_live_question_gets_the_agreed_answer():
    missing = []
    for text, expected in LIVE:
        answer = questions.answer_for(text, CTX)
        if answer is None:
            missing.append((text[:60], "no rule"))
            continue
        if answer.value != expected:
            missing.append((text[:60], f"{answer.value!r}, wanted {expected!r}"))
    assert not missing, missing


def test_the_visa_question_is_not_answered_by_the_generic_sponsorship_rule():
    """The generic rule would say "No" to a question whose answer is "Yes"."""
    text = LIVE[2][0]
    answer = questions.answer_for(text, CTX)
    assert answer is not None
    assert answer.value == "Yes"


def test_the_disability_scheme_question_is_answered_despite_the_protected_rule():
    """Its own wording says "disability"; the question is whether to join."""
    text = LIVE[-1][0]
    assert questions.PROTECTED.search(text) is not None
    answer = questions.answer_for(text, CTX)
    assert answer is not None
    assert answer.value == "No"
    assert answer.confidence == "certain"


def test_a_disability_question_without_a_stored_answer_stays_protected():
    """Nothing is inferred: no answer in the bank means no answer on the form."""
    empty = questions.Context(answers={}, candidate={})
    text = "Do you consider yourself to have a disability?"
    answer = questions.answer_for(text, empty)
    assert answer is not None
    assert answer.value == ""
    assert "protected" in answer.reason
