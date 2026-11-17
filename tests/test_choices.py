"""Choosing an option from a closed list, and remembering the choice."""

from __future__ import annotations

from jobhunter import choices

HOW_HEARD = "How Did You Hear About Us?"


def test_an_exact_match_wins():
    picked = choices.choose(HOW_HEARD, ["Internet", "Company Website"], "Company Website")
    assert picked == ("Company Website", "exact")


def test_case_and_spacing_do_not_matter():
    picked = choices.choose(HOW_HEARD, ["Company  Website"], "company website")
    assert picked == ("Company  Website", "exact")


def test_a_longer_option_that_starts_with_the_answer_is_accepted():
    picked = choices.choose(
        HOW_HEARD, ["Company Website (please specify)"], "Company website"
    )
    assert picked is not None
    assert picked[0].startswith("Company Website")


def test_how_did_you_hear_falls_back_to_a_close_option():
    """"Company website or similar" — he does not care which variant."""
    picked = choices.choose(HOW_HEARD, ["Internet", "Job board", "Other"], "Company website")
    assert picked is not None
    assert picked[0] == "Internet"
    assert "preference" in picked[1]


def test_the_preference_never_picks_other():
    """'Other' usually reveals a free-text box, so it is not equivalent."""
    picked = choices.choose(HOW_HEARD, ["Other"], "Company website")
    assert picked is None


def test_the_preference_does_not_leak_into_other_questions():
    picked = choices.choose("Which office are you applying to?", ["Internet"], "London")
    assert picked is None


def test_an_unrelated_question_gets_no_answer_rather_than_a_near_one():
    picked = choices.choose("Do you hold a driving licence?", ["Yes", "No"], "Maybe")
    assert picked is None


def test_a_remembered_choice_is_reused(tmp_path):
    path = tmp_path / "choices.json"
    choices.remember("mgpru.wd3", HOW_HEARD, "Direct from employer", path=path)
    data = choices.load(path)
    picked = choices.choose(
        HOW_HEARD,
        ["Direct from employer", "Internet"],
        "Company website",
        tenant="mgpru.wd3",
        data=data,
    )
    assert picked == ("Direct from employer", "remembered for mgpru.wd3")


def test_a_remembered_choice_does_not_apply_to_another_tenant(tmp_path):
    path = tmp_path / "choices.json"
    choices.remember("mgpru.wd3", HOW_HEARD, "Direct from employer", path=path)
    data = choices.load(path)
    picked = choices.choose(
        HOW_HEARD,
        ["Direct from employer", "Internet"],
        "Company website",
        tenant="abrdn.wd3",
        data=data,
    )
    assert picked is not None
    assert picked[1] != "remembered for mgpru.wd3"


def test_a_remembered_option_that_disappeared_is_ignored(tmp_path):
    path = tmp_path / "choices.json"
    choices.remember("mgpru.wd3", HOW_HEARD, "Careers fair", path=path)
    picked = choices.choose(
        HOW_HEARD,
        ["Internet", "Other"],
        "Company website",
        tenant="mgpru.wd3",
        data=choices.load(path),
    )
    assert picked is not None
    assert picked[0] == "Internet"
