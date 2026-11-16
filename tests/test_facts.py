"""The fact sheet: written once, read everywhere.

The point of the test suite here is that a stored fact reaches a form field, and
that an unstored protected answer still does not.
"""

from __future__ import annotations

from pathlib import Path

from jobhunter import facts, fill, questions


def test_save_and_load_round_trip(tmp_path):
    facts.save(
        tmp_path,
        {
            "answers": {"phone": "0123", "address": "1 High Street", "postcode": "EX1 1AA"},
            "disclosures": {},
        },
    )
    loaded = facts.load(tmp_path)
    assert loaded["answers"]["phone"] == "0123"
    assert loaded["answers"]["postcode"] == "EX1 1AA"
    assert "disclosures" not in loaded


def test_merge_lets_the_fact_sheet_win(tmp_path):
    facts.save(tmp_path, {"answers": {"phone": "999", "city": "Exeter"}})
    config = {"answers": {"phone": "111", "location": "Exeter, Devon"}}
    merged = facts.merge(config, tmp_path)
    assert merged["answers"]["phone"] == "999"
    assert merged["answers"]["location"] == "Exeter, Devon"
    assert merged["answers"]["city"] == "Exeter"


def test_merge_is_a_no_op_without_a_fact_sheet(tmp_path):
    config = {"answers": {"phone": "111"}}
    assert facts.merge(config, tmp_path) == config


def test_facts_reach_the_form_values(tmp_path):
    facts.save(
        tmp_path,
        {
            "answers": {
                "address": "1 High Street",
                "city": "Exeter",
                "postcode": "EX1 1AA",
                "country": "United Kingdom",
            }
        },
    )
    config = facts.merge({"profile": {"name": "Jed Dahlke"}, "answers": {}}, tmp_path)
    values = fill.values_from(config, cv_path="cv.pdf")
    assert values.address == "1 High Street"
    assert values.postcode == "EX1 1AA"
    assert values.country == "United Kingdom"


def test_stored_disclosure_is_used_but_still_flagged():
    context = questions.Context(disclosures={"veteran_status": "I am not a protected veteran"})
    answer = questions.answer_for("Veteran status", context)
    assert answer is not None
    assert answer.value == "I am not a protected veteran"
    # Still review: a declaration about you is always yours to confirm.
    assert answer.confidence == "review"


def test_protected_question_without_a_stored_answer_stays_blank():
    answer = questions.answer_for("What is your ethnicity?", questions.Context())
    assert answer is not None
    assert answer.value == ""
    assert "protected" in answer.reason


def test_fact_sheet_file_is_not_in_the_repository():
    root = Path(__file__).resolve().parents[1]
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    assert "facts.toml" in ignored or "*.toml" in ignored
