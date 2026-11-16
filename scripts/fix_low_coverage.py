"""One-off: apply the low-coverage rule to rows stored before it existed."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=60)
changed = connection.execute(
    "UPDATE jobs SET decision = 'ask', notes = 'low keyword overlap with the CV; read the posting' "
    "WHERE decision = 'apply' AND coverage IS NOT NULL AND coverage < 0.2"
).rowcount
connection.commit()

print("downgraded:", changed)
for row in connection.execute("SELECT decision, COUNT(1) FROM jobs GROUP BY decision"):
    print(row)
