"""How long are the stored descriptions, by source?"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=30)

print("by source prefix: count, min/median/max length, unscored")
for row in connection.execute(
    """
    SELECT
      CASE
        WHEN source LIKE 'adzuna%' THEN 'adzuna'
        WHEN source LIKE 'reed%' THEN 'reed'
        WHEN source LIKE 'jobspy%' THEN 'jobspy'
        WHEN source LIKE 'seek%' THEN 'seek'
        WHEN source LIKE 'workday%' THEN 'workday'
        WHEN source LIKE 'greenhouse%' THEN 'greenhouse'
        WHEN source LIKE 'import%' THEN 'import'
        ELSE 'other'
      END AS kind,
      COUNT(1) n,
      MIN(LENGTH(description)) lo,
      CAST(AVG(LENGTH(description)) AS INT) avg,
      MAX(LENGTH(description)) hi,
      SUM(CASE WHEN coverage IS NULL THEN 1 ELSE 0 END) unscored
    FROM jobs GROUP BY kind ORDER BY n DESC
    """
):
    print(f"  {row[0]:<12} n={row[1]:<4} min={row[2]:<6} avg={row[3]:<6} max={row[4]:<6} unscored={row[5]}")

print()
print("apply rows that are unscored, by length band:")
for row in connection.execute(
    """
    SELECT CASE
             WHEN LENGTH(description) < 400 THEN '<400'
             WHEN LENGTH(description) < 1200 THEN '400-1200'
             WHEN LENGTH(description) < 3000 THEN '1200-3000'
             ELSE '3000+' END AS band,
           COUNT(1)
    FROM jobs WHERE decision = 'apply' AND coverage IS NULL GROUP BY band ORDER BY 2 DESC
    """
):
    print(f"  {row[0]:<12} {row[1]}")
