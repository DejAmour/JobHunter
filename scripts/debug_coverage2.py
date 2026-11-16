"""Why does a clearly relevant posting score zero?"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobhunter import cv, screening  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=30)
connection.row_factory = sqlite3.Row
row = connection.execute(
    "SELECT * FROM jobs WHERE company = 'AJ Bell' ORDER BY LENGTH(description) DESC LIMIT 1"
).fetchone()

cv_text = cv.extract(Path(r"C:\Users\jedda\dev\jobhunter\output\Jed Dahlke CV.pdf"))[0]
report = screening.screen(row["description"], cv_text)

print("requirements:", len(report.requirements))
for item in report.requirements[:10]:
    print("  -", item[:110].replace("\n", " "))
print("coverage:", report.coverage)
print("decision:", report.decision)
print("missing:", len(report.missing))
