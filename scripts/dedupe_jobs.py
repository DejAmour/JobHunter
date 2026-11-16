"""Remove postings that are the same advert under a different aggregator URL.

Keeps the lowest id of each fingerprint, unless an application is attached to one
of them, in which case that row survives.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobhunter.discover import fingerprint  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=60)
connection.row_factory = sqlite3.Row

rows = connection.execute(
    "SELECT j.id, j.company, j.title, j.location, "
    "(SELECT COUNT(1) FROM applications a WHERE a.job_id = j.id) AS applied "
    "FROM jobs j ORDER BY j.id"
).fetchall()

keep: dict[str, int] = {}
drop: list[int] = []
for row in rows:
    mark = fingerprint(row["company"], row["title"], row["location"])
    if mark not in keep:
        keep[mark] = row["id"]
        continue
    if row["applied"]:
        # An application points at this row; keep it and drop the older one.
        drop.append(keep[mark])
        keep[mark] = row["id"]
    else:
        drop.append(row["id"])

for job_id in drop:
    connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
connection.commit()

print(f"removed {len(drop)} duplicate postings")
print("remaining:", connection.execute("SELECT COUNT(1) FROM jobs").fetchone()[0])
