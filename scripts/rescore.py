"""Re-screen every stored posting with the current rules.

Scores written by an older version of the coverage heuristic are not comparable
with new ones, so after changing the heuristic the whole store is recomputed
rather than left half-measured. Decisions are recomputed too, with the same
policy discovery uses: a low-overlap "apply" becomes "ask".
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobhunter import cv, screening  # noqa: E402
from jobhunter.discover import MIN_COVERAGE  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CV_PATH = Path(r"C:\Users\jedda\dev\jobhunter\output\Jed Dahlke CV.pdf")
cv_text = cv.extract(CV_PATH)[0]

connection = sqlite3.connect(Path.home() / ".jobhunter" / "jobs.db", timeout=60)
connection.row_factory = sqlite3.Row
with connection:
    rows = connection.execute("SELECT id, company, title, description, decision FROM jobs").fetchall()

changed_decision = 0
scored = 0
before = {row["decision"]: 0 for row in rows}
for row in rows:
    before[row["decision"]] = before.get(row["decision"], 0) + 1

for row in rows:
    if not row["description"]:
        continue
    report = screening.screen(row["description"], cv_text)
    decision = report.decision
    note = ""
    if decision == "apply" and report.coverage is not None and report.coverage < MIN_COVERAGE:
        decision = "ask"
        note = "low keyword overlap with the CV; read the posting"
    if len(row["description"]) < 400 and decision == "apply":
        decision = "ask"
        note = "short listing text only; open the posting before applying"
    if decision != row["decision"]:
        changed_decision += 1
    if report.coverage is not None:
        scored += 1
    connection.execute(
        "UPDATE jobs SET decision = ?, coverage = ?, notes = ? WHERE id = ?",
        (decision, report.coverage, note, row["id"]),
    )

connection.commit()

print(f"rescreened {len(rows)} postings, {scored} now scored")
print(f"decisions changed: {changed_decision}")
after = {
    row[0]: row[1]
    for row in connection.execute("SELECT decision, COUNT(1) FROM jobs GROUP BY decision")
}
print("before:", before)
print("after: ", after)
