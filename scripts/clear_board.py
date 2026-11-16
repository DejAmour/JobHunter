"""Delete stored postings from one board so a tightened filter can be re-run."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

board = sys.argv[1] if len(sys.argv) > 1 else "seek"
connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=60)
removed = connection.execute(
    "DELETE FROM jobs WHERE source LIKE ? AND id NOT IN (SELECT job_id FROM applications WHERE job_id IS NOT NULL)",
    (f"{board}:%",),
).rowcount
connection.commit()
print(f"removed {removed} rows from {board}")
print("remaining:", connection.execute("SELECT COUNT(1) FROM jobs").fetchone()[0])
