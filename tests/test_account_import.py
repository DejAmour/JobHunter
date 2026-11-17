"""Taking one employer's password out of a manager's CSV, and nothing else.

Chrome and Google Password Manager export every saved password at once. An
importer that trusts the file would happily copy a bank login into the credential
store, so these tests are mostly about refusal.
"""

from __future__ import annotations

import pytest

from jobhunter import accounts, credentials


@pytest.fixture()
def csv_file(tmp_path):
    path = tmp_path / "passwords.csv"
    path.write_text(
        "name,url,username,password,note\n"
        "M&G,https://mgpru.wd3.myworkdayjobs.com/,candidate@example.com,Workday-Pass-1,\n"
        "Monzo,https://monzo.com/login,candidate@example.com,Bank-Pass-9,\n"
        "abrdn,https://abrdn.wd3.myworkdayjobs.com/,candidate@example.com,Other-Pass-2,\n",
        encoding="utf-8",
    )
    return path


def test_rows_are_read_with_lower_case_headers(csv_file):
    rows = accounts.csv_rows(csv_file)
    assert len(rows) == 3
    assert rows[0]["name"] == "M&G"
    assert rows[0]["password"] == "Workday-Pass-1"


def test_the_tenant_url_identifies_the_row(csv_file):
    account = accounts.Account(
        ats="workday", slug="m-g", firm="M&G", email="candidate@example.com",
        tenant="mgpru.wd3",
    )
    row = accounts.match_row(accounts.csv_rows(csv_file), account)
    assert row is not None
    assert row["password"] == "Workday-Pass-1"


def test_a_two_letter_firm_name_alone_is_not_enough(tmp_path):
    """'M&G' normalises to 'mg', which appears in almost anything."""
    path = tmp_path / "passwords.csv"
    path.write_text(
        "name,url,username,password,note\n"
        "Imgur,https://imgur.com/,someone@else.test,unrelated-password,\n",
        encoding="utf-8",
    )
    account = accounts.Account(
        ats="workday", slug="m-g", firm="M&G", email="candidate@example.com", tenant=""
    )
    assert accounts.match_row(accounts.csv_rows(path), account) is None


def test_a_different_employer_is_not_taken(csv_file):
    account = accounts.Account(
        ats="workday", slug="schroders", firm="Schroders", email="candidate@example.com",
        tenant="",
    )
    assert accounts.match_row(accounts.csv_rows(csv_file), account) is None


@pytest.mark.skipif(not credentials.available(), reason="no credential store on this machine")
def test_import_stores_only_the_matched_password(tmp_path, csv_file):
    account = accounts.add(tmp_path, "M&G", "candidate@example.com")
    try:
        taken = accounts.import_password(tmp_path, csv_file, "M&G")
        assert taken is not None
        stored, label = taken
        assert stored.slug == "m-g"
        assert label == "M&G"
        assert accounts.password(account) == "Workday-Pass-1"
        # The bank row was in the same file and was not touched.
        assert credentials.load("workday", "monzo") is None
    finally:
        accounts.remove(tmp_path, account.firm)


@pytest.mark.skipif(not credentials.available(), reason="no credential store on this machine")
def test_import_declines_when_the_account_is_not_registered(tmp_path, csv_file):
    assert accounts.import_password(tmp_path, csv_file, "M&G") is None
