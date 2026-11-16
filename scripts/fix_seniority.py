"""One-off: apply the seniority rule to rows discovered before it existed."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=60)
changed = connection.execute(
    "UPDATE jobs SET decision = 'ask', notes = 'senior title; check the level before applying' "
    "WHERE seniority = 'senior' AND decision = 'apply'"
).rowcount
connection.commit()

print("downgraded:", changed)
for row in connection.execute("SELECT decision, COUNT(1) FROM jobs GROUP BY decision"):
    print(row)
