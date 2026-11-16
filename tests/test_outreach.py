import pytest

from jobhunter import db, outreach

PAGE = """
<p>Questions? Email our recruiter at jane.smith@acme.com.</p>
<a href="mailto:talent@acme.com">Talent team</a>
<p>no-reply@acme.com is not monitored.</p>
"""


def test_finds_addresses_and_classifies_roles():
    found = outreach.find_contacts(PAGE, "Acme")
    emails = {c.email for c in found}
    assert "jane.smith@acme.com" in emails
    assert "talent@acme.com" in emails
    assert all(c.verified for c in found)
    roles = {c.email: c.role for c in found}
    assert roles["talent@acme.com"] == "talent"


def test_guesses_are_never_verified():
    guesses = outreach.guess_contacts("Acme", "acme.com", "Jane Smith")
    assert guesses
    assert not any(g.verified for g in guesses)


def test_draft_needs_a_concrete_reason():
    contact = outreach.Contact(email="a@b.com", verified=True)
    with pytest.raises(outreach.OutreachRefused):
        outreach.draft(contact, "Acme", "Backend Engineer", "short")
    letter = outreach.draft(contact, "Acme", "Backend Engineer", "your payments API rebuild is the kind of problem I want")
    assert "Acme" in letter.subject
    assert "payments API rebuild" in letter.body


def test_dry_run_records_and_never_transmits(tmp_path):
    store = tmp_path / "jobs.db"
    db.init(store)
    settings = outreach.Settings(per_domain_per_day=2, per_run=5)
    profile = outreach.Profile(name="J", email="me@mine.dev")
    contact = outreach.Contact(email="jane@acme.com", company="Acme", verified=True)
    letter = outreach.draft(contact, "Acme", "Backend Engineer", "your payments API rebuild caught my eye")

    with db.connect(store) as connection:
        outcome = outreach.send(connection, settings, profile, contact, letter, dry_run=True)
        assert outcome == "dry-run"
        rows = connection.execute("SELECT status, dry_run FROM outreach").fetchall()
    assert [tuple(row) for row in rows] == [("dry-run", 1)]


def test_unverified_is_refused(tmp_path):
    store = tmp_path / "jobs.db"
    db.init(store)
    settings = outreach.Settings(per_domain_per_day=2, per_run=5)
    contact = outreach.Contact(email="guess@acme.com", verified=False, source="guessed")
    letter = outreach.draft(contact, "Acme", "Backend Engineer", "your payments API rebuild caught my eye")

    with db.connect(store) as connection:
        with pytest.raises(outreach.OutreachRefused):
            outreach.send(connection, settings, outreach.Profile("J", "me@mine.dev"), contact, letter, dry_run=True)


def test_no_reply_addresses_are_refused(tmp_path):
    store = tmp_path / "jobs.db"
    db.init(store)
    settings = outreach.Settings(per_domain_per_day=2, per_run=5)
    contact = outreach.Contact(email="no-reply@acme.com", verified=True)
    letter = outreach.draft(contact, "Acme", "Backend Engineer", "your payments API rebuild caught my eye")

    with db.connect(store) as connection:
        with pytest.raises(outreach.OutreachRefused):
            outreach.send(connection, settings, outreach.Profile("J", "me@mine.dev"), contact, letter, dry_run=True)
