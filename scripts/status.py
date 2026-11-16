"""Where the pipeline actually stands, in four numbers."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=30)

queries = {
    "postings": "SELECT COUNT(1) FROM jobs",
    "apply": "SELECT COUNT(1) FROM jobs WHERE decision = 'apply'",
    "ask": "SELECT COUNT(1) FROM jobs WHERE decision = 'ask'",
    "scored (has coverage)": "SELECT COUNT(1) FROM jobs WHERE coverage IS NOT NULL",
    "apply but unscored": "SELECT COUNT(1) FROM jobs WHERE decision = 'apply' AND coverage IS NULL",
    "applications": "SELECT COUNT(1) FROM applications",
    "contacts": "SELECT COUNT(1) FROM contacts",
    "outreach sent": "SELECT COUNT(1) FROM outreach WHERE dry_run = 0",
}
for label, sql in queries.items():
    print(f"{label:<22} {connection.execute(sql).fetchone()[0]}")
