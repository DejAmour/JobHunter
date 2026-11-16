"""Print what is in the store. Handy after a discovery run."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".jobhunter" / "jobs.db"
connection = sqlite3.connect(path)
connection.row_factory = sqlite3.Row

print(f"store: {path}")
print(f"total jobs: {connection.execute('SELECT COUNT(*) n FROM jobs').fetchone()['n']}")

print("\nby decision / country")
for row in connection.execute(
    "SELECT decision, country, COUNT(*) n FROM jobs GROUP BY decision, country ORDER BY n DESC"
):
    print(f"  {str(row['decision']):<8} {str(row['country']):<4} {row['n']:>4}")

print("\nby employer")
for row in connection.execute(
    "SELECT company, COUNT(*) n, MAX(decision) d FROM jobs GROUP BY company ORDER BY n DESC LIMIT 20"
):
    print(f"  {row['company'][:38]:<38} {row['n']:>4}")

print("\napply / ask, UK")
for row in connection.execute(
    "SELECT title, company, location, decision, coverage FROM jobs "
    "WHERE country = 'uk' AND decision IN ('apply','ask') ORDER BY coverage DESC LIMIT 25"
):
    print(f"  {(row['coverage'] or 0):>4.0%} {row['decision']:<6} {row['company'][:26]:<26} {row['title'][:46]}")

print("\napply / ask, AU")
for row in connection.execute(
    "SELECT title, company, location, decision, coverage FROM jobs "
    "WHERE country = 'au' AND decision IN ('apply','ask') ORDER BY coverage DESC LIMIT 25"
):
    print(f"  {(row['coverage'] or 0):>4.0%} {row['decision']:<6} {row['company'][:26]:<26} {row['title'][:46]}")
