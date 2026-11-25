"""The postings list: ordering by the date the employer listed the role.

"Newest" has to mean the posting's own date. Discovery date is a different fact:
a role found today may have been advertised three weeks ago, and sorting by when
we noticed it puts an old advert at the top of a "newest" list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobhunter import db, web


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "jobs.db"
    db.init(path)
    return path


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[profile]\nname = "Jed Dahlke"\ncv = ""\n\n[search]\nterms = []\n', encoding="utf-8")
    return path


def add(store: Path, title: str, posted_at: str | None, company: str = "Acme") -> int:
    with db.connect(store) as connection:
        return db.upsert_job(
            connection,
            url=f"https://example.test/{title.replace(' ', '-').lower()}",
            company=company,
            title=title,
            posted_at=posted_at,
            decision="apply",
        )


def order_of(body: str, titles: list[str]) -> list[str]:
    return sorted(titles, key=body.index)


def test_newest_means_the_posting_date_not_the_discovery_date(store, config_path):
    add(store, "Oldest Role", "2026-10-01")
    add(store, "Middle Role", "2026-11-10")
    add(store, "Newest Role", "2026-11-24")
    add(store, "Undated Role", None)

    client = web.create_app(store, config_path).test_client()
    body = client.get("/jobs?sort=newest").get_data(as_text=True)

    titles = ["Oldest Role", "Middle Role", "Newest Role", "Undated Role"]
    assert order_of(body, titles) == ["Newest Role", "Middle Role", "Oldest Role", "Undated Role"]


def test_oldest_puts_the_undated_row_last(store, config_path):
    add(store, "Older Role", "2026-09-01")
    add(store, "Newer Role", "2026-11-01")
    add(store, "No Date Role", None)

    client = web.create_app(store, config_path).test_client()
    body = client.get("/jobs?sort=oldest").get_data(as_text=True)

    titles = ["Older Role", "Newer Role", "No Date Role"]
    assert order_of(body, titles) == ["Older Role", "Newer Role", "No Date Role"]


def test_an_unknown_sort_falls_back_to_best_match(store, config_path):
    add(store, "A Role", "2026-11-01")
    add(store, "B Role", "2026-11-02")

    client = web.create_app(store, config_path).test_client()
    response = client.get("/jobs?sort=; DROP TABLE jobs")
    assert response.status_code == 200
    assert "A Role" in response.get_data(as_text=True)


def test_the_list_shows_the_listed_date_and_keeps_the_sort_when_paging(store, config_path):
    for index in range(80):
        add(store, f"Role {index:02d}", f"2026-11-{(index % 28) + 1:02d}")

    client = web.create_app(store, config_path).test_client()
    body = client.get("/jobs?sort=newest").get_data(as_text=True)

    assert "Listed" in body
    assert "2026-11-" in body
    # The next-page link must carry the sort, or page 2 silently reorders.
    assert "sort=newest" in body


def test_listed_date_translates_the_boards_two_languages():
    assert db.listed_date("2026-11-24", "2026-11-25") == "2026-11-24"
    assert db.listed_date("Posted Today", "2026-11-25") == "2026-11-25"
    assert db.listed_date("Posted Yesterday", "2026-11-25") == "2026-11-24"
    assert db.listed_date("Posted 8 Days Ago", "2026-11-25") == "2026-11-17"
    assert db.listed_date("Posted 30+ Days Ago", "2026-11-25") == "2026-10-26"
    # No posting date at all: unknown, so it sorts last rather than looking new.
    assert db.listed_date(None, "2026-11-25T10:00:00") is None


def test_workday_relative_dates_sort_with_iso_dates(store, config_path):
    """Without conversion, "Posted 20 Days Ago" sorts above "2026-11-24"."""
    ids = {}
    for title, posted, discovered in (
        ("Recent ISO", "2026-11-24", "2026-11-25"),
        ("Old Relative", "Posted 20 Days Ago", "2026-11-16"),
        ("New Relative", "Posted 2 Days Ago", "2026-11-16"),
    ):
        ids[title] = add(store, title, posted)
        with db.connect(store) as connection:
            connection.execute(
                "UPDATE jobs SET discovered_at = ? WHERE id = ?", (discovered, ids[title])
            )

    client = web.create_app(store, config_path).test_client()
    body = client.get("/jobs?sort=newest").get_data(as_text=True)

    titles = ["Recent ISO", "Old Relative", "New Relative"]
    assert order_of(body, titles) == ["Recent ISO", "New Relative", "Old Relative"]
