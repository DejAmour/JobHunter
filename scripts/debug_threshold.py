"""Why are sub-threshold rows still marked apply?"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobhunter.discover import MIN_COVERAGE  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

print("MIN_COVERAGE =", MIN_COVERAGE)

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=30)
connection.row_factory = sqlite3.Row

rows = connection.execute(
    "SELECT id, company, title, decision, coverage, notes FROM jobs "
    "WHERE decision = 'apply' AND coverage IS NOT NULL AND coverage < ? LIMIT 8",
    (MIN_COVERAGE,),
).fetchall()
print(f"{len(rows)} rows below threshold still marked apply")
for row in rows:
    print(f"  {row['coverage']:.2f}  {row['company'][:20]:<20} {row['title'][:40]}")
    print(f"        notes: {row['notes']!r}")
