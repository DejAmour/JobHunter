"""One-off: a posting with no stored text is 'ask', not 'apply'."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=60)
changed = connection.execute(
    "UPDATE jobs SET decision = 'ask', "
    "notes = CASE WHEN notes = '' THEN 'no listing text stored; open the posting before applying' ELSE notes END "
    "WHERE decision = 'apply' AND LENGTH(TRIM(description)) < 200"
).rowcount
connection.commit()
print("downgraded:", changed)
for row in connection.execute("SELECT country, decision, COUNT(1) FROM jobs GROUP BY country, decision"):
    print(row)
