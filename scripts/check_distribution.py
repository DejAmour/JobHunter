"""Distribution of coverage, to pick a sensible 'apply' threshold."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=30)

bands = [(None, "unscored"), (0.0, "0-10%"), (0.1, "10-20%"), (0.2, "20-30%"),
         (0.3, "30-40%"), (0.4, "40-50%"), (0.5, "50%+")]

print("coverage distribution among decision = 'apply':")
for start, label in bands:
    if start is None:
        count = connection.execute(
            "SELECT COUNT(1) FROM jobs WHERE decision = 'apply' AND coverage IS NULL"
        ).fetchone()[0]
    else:
        upper = {"0-10%": 0.1, "10-20%": 0.2, "20-30%": 0.3, "30-40%": 0.4,
                 "40-50%": 0.5, "50%+": 2.0}[label]
        count = connection.execute(
            "SELECT COUNT(1) FROM jobs WHERE decision = 'apply' AND coverage >= ? AND coverage < ?",
            (start, upper),
        ).fetchone()[0]
    print(f"  {label:<10} {count}")

print("\nhow many would survive each threshold:")
for threshold in (0.2, 0.25, 0.3, 0.35, 0.4):
    count = connection.execute(
        "SELECT COUNT(1) FROM jobs WHERE decision IN ('apply','ask') AND coverage >= ?", (threshold,)
    ).fetchone()[0]
    print(f"  >= {threshold:.0%}: {count}")
