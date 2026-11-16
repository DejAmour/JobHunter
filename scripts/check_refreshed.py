"""What did refresh actually store? Print the head of two rows."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=30)
connection.row_factory = sqlite3.Row

for row in connection.execute(
    "SELECT title, company, url, description, coverage, decision FROM jobs "
    "WHERE source LIKE 'adzuna%' OR source LIKE 'reed%' ORDER BY LENGTH(description) DESC LIMIT 3"
):
    print("=" * 70)
    print(f"{row['title']} — {row['company']}")
    print(f"url: {row['url'][:90]}")
    print(f"coverage: {row['coverage']}  decision: {row['decision']}  chars: {len(row['description'])}")
    print("--- text ---")
    print(row["description"][:600].replace("\n", " | "))
