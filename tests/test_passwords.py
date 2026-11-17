"""Generated passwords: strong, typable, and exportable to a real manager."""

from __future__ import annotations

import csv
import re

import pytest

from jobhunter import accounts, credentials


def test_a_generated_password_satisfies_the_rules_employers_print():
    password = accounts.generate_password(20)
    assert len(password) == 20
    assert re.search(r"[A-Z]", password)
    assert re.search(r"[a-z]", password)
    assert re.search(r"[0-9]", password)
    assert re.search(r"[^A-Za-z0-9]", password)


def test_no_lookalike_characters():
    """It has to be typeable by hand when an account locks."""
    password = accounts.generate_password(40)
    assert not set(password) & set(accounts.LOOKALIKES)


def test_generated_passwords_differ():
    assert len({accounts.generate_password(16) for _ in range(50)}) == 50


def test_a_short_password_is_refused():
    with pytest.raises(ValueError):
        accounts.generate_password(8)


@pytest.mark.skipif(not credentials.available(), reason="no credential store on this machine")
def test_export_writes_a_manager_importable_row(tmp_path):
    account = accounts.add(tmp_path, "M&G", "candidate@example.com", password="hunter2!")
    try:
        out = tmp_path / "passwords.csv"
        assert accounts.export_csv(tmp_path, out) == 1
        with out.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows == [
            {
                "name": "M&G (workday)",
                "url": "",
                "username": "candidate@example.com",
                "password": "hunter2!",
                "notes": "Employer login managed by jobhunter.",
            }
        ]
    finally:
        accounts.remove(tmp_path, account.firm)
