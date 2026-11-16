"""Screen one stored row and print exactly what the code decides."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobhunter import cv, screening  # noqa: E402
from jobhunter.discover import MIN_COVERAGE  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=30)
connection.row_factory = sqlite3.Row
row = connection.execute("SELECT * FROM jobs WHERE company = 'Phoenix Group' LIMIT 1").fetchone()

cv_text = cv.extract(Path(r"C:\Users\jedda\dev\jobhunter\output\Jed Dahlke CV.pdf"))[0]
report = screening.screen(row["description"], cv_text)

print("title:", row["title"])
print("stored coverage:", row["coverage"])
print("report coverage:", report.coverage)
print("report decision:", report.decision)
print("MIN_COVERAGE:", MIN_COVERAGE)
print("would demote:", report.decision == "apply" and report.coverage is not None and report.coverage < MIN_COVERAGE)
