"""The local store. One SQLite file, no server, no sync.

Append-only where it matters: an application or an outreach message is a record
of something that happened, so rows are written once and never rewritten. The
ledger is the only place a submission is proven.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,
    company      TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    location     TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT '',
    posted_at    TEXT,
    description  TEXT NOT NULL DEFAULT '',
    decision     TEXT,
    coverage     REAL,
    discovered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER REFERENCES jobs(id),
    status       TEXT NOT NULL,
    approval     TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    proof_path   TEXT,
    note         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contacts (
    id        INTEGER PRIMARY KEY,
    company   TEXT NOT NULL DEFAULT '',
    email     TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL DEFAULT '',
    role      TEXT NOT NULL DEFAULT '',
    source    TEXT NOT NULL DEFAULT '',
    verified  INTEGER NOT NULL DEFAULT 0,
    found_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach (
    id         INTEGER PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id),
    job_id     INTEGER REFERENCES jobs(id),
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL,
    dry_run    INTEGER NOT NULL DEFAULT 1,
    sent_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS answers (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS contacts_domain ON contacts(email);
CREATE INDEX IF NOT EXISTS outreach_sent ON outreach(sent_at);

CREATE TABLE IF NOT EXISTS assessments (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER REFERENCES applications(id),
    kind           TEXT NOT NULL DEFAULT 'online',
    due_at         TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interviews (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER REFERENCES applications(id),
    round          TEXT NOT NULL DEFAULT 'first',
    scheduled_at   TEXT,
    mode           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'scheduled',
    note           TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cv_versions (
    id          INTEGER PRIMARY KEY,
    label       TEXT NOT NULL,
    market      TEXT NOT NULL DEFAULT 'uk',
    path        TEXT NOT NULL,
    pages       INTEGER,
    parse_score INTEGER,
    built_at    TEXT NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(label, market)
);
"""

# Columns added after the first release. ``ALTER TABLE`` is a no-op if the column
# is already there, but SQLite has no IF NOT EXISTS for it, so the set is checked
# against pragma instead of swallowed in a try/except.
LATER_COLUMNS = {
    "jobs": {
        "country": "TEXT NOT NULL DEFAULT ''",
        "department": "TEXT NOT NULL DEFAULT ''",
        "seniority": "TEXT NOT NULL DEFAULT ''",
        "notes": "TEXT NOT NULL DEFAULT ''",
    },
    "applications": {
        "stage": "TEXT NOT NULL DEFAULT 'draft'",
        "variant": "TEXT NOT NULL DEFAULT ''",
        "country": "TEXT NOT NULL DEFAULT ''",
        "deadline": "TEXT",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
    },
}


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    # WAL is what lets the dashboard read while a discovery run writes. Without
    # it a long scrape holds the write lock and every page load times out.
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass  # another process is mid-write; the default journal still works
    return connection


def init(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        for table, columns in LATER_COLUMNS.items():
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            for column, spec in columns.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")


def upsert_job(
    connection: sqlite3.Connection,
    *,
    url: str,
    company: str = "",
    title: str = "",
    location: str = "",
    source: str = "",
    description: str = "",
    posted_at: str | None = None,
    decision: str | None = None,
    coverage: float | None = None,
    country: str = "",
    department: str = "",
    seniority: str = "",
) -> int:
    connection.execute(
        """
        INSERT INTO jobs (
            url, company, title, location, source, description, posted_at,
            decision, coverage, discovered_at, country, department, seniority
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            decision = COALESCE(excluded.decision, jobs.decision),
            coverage = COALESCE(excluded.coverage, jobs.coverage),
            country = CASE WHEN excluded.country <> '' THEN excluded.country ELSE jobs.country END,
            department = CASE WHEN excluded.department <> '' THEN excluded.department ELSE jobs.department END,
            seniority = CASE WHEN excluded.seniority <> '' THEN excluded.seniority ELSE jobs.seniority END,
            description = CASE WHEN excluded.description <> '' THEN excluded.description ELSE jobs.description END
        """,
        (
            url, company, title, location, source, description, posted_at,
            decision, coverage, now(), country, department, seniority,
        ),
    )
    row = connection.execute("SELECT id FROM jobs WHERE url = ?", (url,)).fetchone()
    return int(row["id"])


def record_contact(
    connection: sqlite3.Connection,
    *,
    email: str,
    company: str = "",
    name: str = "",
    role: str = "",
    source: str = "",
    verified: bool = False,
) -> int:
    connection.execute(
        """
        INSERT INTO contacts (email, company, name, role, source, verified, found_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE contacts.name END,
            role = CASE WHEN excluded.role <> '' THEN excluded.role ELSE contacts.role END,
            verified = MAX(contacts.verified, excluded.verified)
        """,
        (email, company, name, role, source, 1 if verified else 0, now()),
    )
    return int(connection.execute("SELECT id FROM contacts WHERE email = ?", (email,)).fetchone()["id"])


def record_outreach(
    connection: sqlite3.Connection,
    *,
    contact_id: int,
    job_id: int | None,
    subject: str,
    body: str,
    status: str,
    dry_run: bool,
) -> int:
    cursor = connection.execute(
        "INSERT INTO outreach (contact_id, job_id, subject, body, status, dry_run, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (contact_id, job_id, subject, body, status, 1 if dry_run else 0, now()),
    )
    return int(cursor.lastrowid or 0)


def record_application(
    connection: sqlite3.Connection,
    *,
    job_id: int,
    status: str,
    approval: str,
    proof_path: str | None = None,
    note: str = "",
    stage: str = "draft",
    variant: str = "",
    country: str = "",
    deadline: str | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO applications (job_id, status, approval, submitted_at, proof_path, note, stage, variant, country, deadline, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, status, approval, now(), proof_path, note, stage, variant, country, deadline, now()),
    )
    return int(cursor.lastrowid or 0)


def set_application_stage(connection: sqlite3.Connection, application_id: int, stage: str) -> None:
    connection.execute(
        "UPDATE applications SET stage = ?, updated_at = ? WHERE id = ?",
        (stage, now(), application_id),
    )


def add_assessment(
    connection: sqlite3.Connection,
    *,
    application_id: int,
    kind: str = "online",
    due_at: str | None = None,
    note: str = "",
) -> int:
    cursor = connection.execute(
        "INSERT INTO assessments (application_id, kind, due_at, status, note, created_at) VALUES (?, ?, ?, 'pending', ?, ?)",
        (application_id, kind, due_at, note, now()),
    )
    return int(cursor.lastrowid or 0)


def add_interview(
    connection: sqlite3.Connection,
    *,
    application_id: int,
    round: str = "first",
    scheduled_at: str | None = None,
    mode: str = "",
    note: str = "",
) -> int:
    cursor = connection.execute(
        "INSERT INTO interviews (application_id, round, scheduled_at, mode, status, note, created_at) VALUES (?, ?, ?, ?, 'scheduled', ?, ?)",
        (application_id, round, scheduled_at, mode, note, now()),
    )
    return int(cursor.lastrowid or 0)


def record_cv(
    connection: sqlite3.Connection,
    *,
    label: str,
    market: str,
    path: str,
    pages: int | None,
    parse_score: int | None,
) -> None:
    connection.execute(
        """
        INSERT INTO cv_versions (label, market, path, pages, parse_score, built_at, is_default)
        VALUES (?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(label, market) DO UPDATE SET
            path = excluded.path,
            pages = excluded.pages,
            parse_score = excluded.parse_score,
            built_at = excluded.built_at
        """,
        (label, market, path, pages, parse_score, now()),
    )


def domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def sent_today_to_domain(connection: sqlite3.Connection, address_domain: str) -> int:
    """Real sends to one domain since midnight UTC."""
    row = connection.execute(
        """
        SELECT COUNT(*) AS n FROM outreach o
        JOIN contacts c ON c.id = o.contact_id
        WHERE o.dry_run = 0 AND o.status = 'sent'
          AND lower(substr(c.email, instr(c.email, '@') + 1)) = ?
          AND substr(o.sent_at, 1, 10) = substr(?, 1, 10)
        """,
        (address_domain.lower(), now()),
    ).fetchone()
    return int(row["n"])


def sent_today(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM outreach WHERE dry_run = 0 AND status = 'sent' AND substr(sent_at, 1, 10) = substr(?, 1, 10)",
        (now(),),
    ).fetchone()
    return int(row["n"])


def set_answer(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO answers (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now()),
    )


def get_answer(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM answers WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None
