"""Why is coverage empty? Screen one stored posting and show the working."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobhunter import cv, screening  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db")
connection.row_factory = sqlite3.Row
row = connection.execute(
    "SELECT * FROM jobs WHERE company = 'Man Group' AND description <> '' ORDER BY length(description) DESC LIMIT 1"
).fetchone()

print("job:", row["title"], "| description chars:", len(row["description"]))
print("first 300:", row["description"][:300].replace("\n", " "))

cv_text, _, _ = cv.extract(Path(r"C:\Users\jedda\dev\jobhunter\output\Jed Dahlke CV.pdf"))
print("cv chars:", len(cv_text))

report = screening.screen(row["description"], cv_text, screening.Candidate(years_experience=3.0, right_to_work=True))
print("requirements:", len(report.requirements))
for requirement in report.requirements[:8]:
    print("  -", requirement[:100])
print("coverage:", report.coverage)
