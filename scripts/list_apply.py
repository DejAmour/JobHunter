"""Show the postings currently marked 'apply', newest first."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=30)
connection.row_factory = sqlite3.Row

for row in connection.execute(
    "SELECT company, title, location, coverage, url FROM jobs "
    "WHERE decision = 'apply' ORDER BY coverage DESC NULLS LAST, company"
):
    coverage = f"{row['coverage']:.0%}" if row["coverage"] is not None else "  -"
    print(f"{coverage:>4}  {row['company'][:26]:<26} {row['title'][:56]:<56} {row['location'][:20]}")
