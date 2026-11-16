"""One-off: apply the broadened 'senior' rule to rows stored before it existed."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=60)
changed = connection.execute(
    "UPDATE jobs SET seniority = 'senior', decision = 'ask', "
    "notes = 'senior title; check the level before applying' "
    "WHERE decision = 'apply' AND LOWER(title) LIKE '%senior%'"
).rowcount
connection.commit()
print("downgraded:", changed)
for row in connection.execute("SELECT country, decision, COUNT(1) FROM jobs GROUP BY country, decision"):
    print(row)
