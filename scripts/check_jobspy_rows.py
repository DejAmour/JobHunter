"""Quality check on the JobSpy rows: descriptions, direct URLs, duplicates."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=30)

total = connection.execute("SELECT COUNT(1) FROM jobs").fetchone()[0]
jobspy = connection.execute("SELECT COUNT(1) FROM jobs WHERE source LIKE 'jobspy:%'").fetchone()[0]
described = connection.execute(
    "SELECT COUNT(1) FROM jobs WHERE source LIKE 'jobspy:%' AND LENGTH(description) > 400"
).fetchone()[0]
direct = connection.execute(
    "SELECT COUNT(1) FROM jobs WHERE source LIKE 'jobspy:%' AND url NOT LIKE '%indeed%'"
).fetchone()[0]

print(f"total postings:     {total}")
print(f"from jobspy:        {jobspy}")
print(f"  with description: {described}")
print(f"  employer URL:     {direct}")

print("\nby decision:")
for row in connection.execute(
    "SELECT country, decision, COUNT(1) FROM jobs GROUP BY country, decision"
):
    print(f"  {row[0]} {row[1]:<6} {row[2]}")

print("\nstrongest jobspy matches:")
for row in connection.execute(
    "SELECT company, title, coverage, decision FROM jobs "
    "WHERE source LIKE 'jobspy:%' AND coverage IS NOT NULL "
    "ORDER BY coverage DESC LIMIT 8"
):
    print(f"  {row[2]:.0%}  {row[0][:24]:<24} {row[1][:54]}")
