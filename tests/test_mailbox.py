"""Pulling a verification code out of an email, without touching the inbox."""

from __future__ import annotations

from jobhunter import mailbox


def test_six_digit_code():
    assert mailbox.extract_code("Your code is 481920. It expires in 10 minutes.") == "481920"


def test_alphanumeric_code_on_a_labelled_line():
    body = "Workday\n\nVerification code: K7Q2P9\n\nEnter it to finish."
    assert mailbox.extract_code(body) == "K7Q2P9"


def test_the_line_about_verification_wins_over_a_stray_number():
    body = "Order 123456 was received.\nYour verification code is 998877."
    assert mailbox.extract_code(body) == "998877"


def test_a_code_must_contain_a_digit():
    assert mailbox.extract_code("Verification code: ABCDEF") == ""


def test_nothing_resembling_a_code_returns_empty():
    assert mailbox.extract_code("Thanks for applying to M&G.") == ""


def test_no_mailbox_configured_means_no_code_is_fetched():
    assert mailbox.available({}) is False
    assert mailbox.latest_code({}) is None


def test_the_address_defaults_to_the_profile_email():
    assert mailbox.address({"profile": {"email": "candidate@example.com"}}) == "candidate@example.com"
    assert mailbox.address({"mail": {"address": "other@example.com"}}) == "other@example.com"


def test_a_configured_imap_host_is_used():
    assert mailbox.host({}) == "imap.gmail.com"
    assert mailbox.host({"mail": {"imap": "imap.example.com"}}) == "imap.example.com"


def test_the_reset_link_beats_the_social_links():
    body = (
        "Reset your password https://mgpru.wd3.myworkdayjobs.com/reset?token=abc "
        "Follow us https://twitter.com/mandg https://linkedin.com/company/mandg"
    )
    links = mailbox.extract_links(body, tenant="mgpru")
    assert links[0].startswith("https://mgpru.wd3.myworkdayjobs.com/reset")


def test_a_link_wrapped_in_punctuation_is_still_found():
    links = mailbox.extract_links("Click here: https://x.test/reset?t=1).", tenant="x")
    assert links == ["https://x.test/reset?t=1"]


def test_no_links_means_no_link():
    assert mailbox.extract_links("Thanks for applying to M&G.") == []


def test_an_anchor_href_survives_when_links_are_wanted():
    """Workday's HTML mail puts the URL in the href and words in the text."""
    html = '<p>Reset it <a href="https://mgpru.wd3.myworkdayjobs.com/reset?t=9">here</a></p>'
    text = mailbox._html_text(html, keep_links=True)
    assert "https://mgpru.wd3.myworkdayjobs.com/reset?t=9" in text
    # ... and is dropped when only the prose matters.
    assert "myworkdayjobs" not in mailbox._html_text(html, keep_links=False)


def test_a_mailbox_without_credentials_fetches_no_link():
    assert mailbox.latest_link({}) is None
