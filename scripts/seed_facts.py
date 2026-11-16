"""Seed the fact sheet from what the repository already knows.

Run once: `python scripts/seed_facts.py`. Anything not known is left out, so
`jobhunter facts` asks for it rather than inventing it.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobhunter import facts  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

home = Path.home() / ".jobhunter"
root = Path(__file__).resolve().parents[1]

config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
contact = tomllib.loads((root / "cv" / "contact.toml").read_text(encoding="utf-8"))
answers = config.get("answers", {})
profile = config.get("profile", {})

seed = {
    "full_name": profile.get("name", ""),
    "email": profile.get("email", ""),
    "phone": answers.get("phone", ""),
    "location": answers.get("location", ""),
    "country": answers.get("country", ""),
    "current_employer": answers.get("current_employer", ""),
    "current_title": answers.get("current_title", ""),
    "notice_period": answers.get("notice_period", ""),
    "highest_qualification": answers.get("highest_qualification", ""),
    "professional_qualifications": answers.get("professional_qualifications", ""),
    "willing_to_relocate": answers.get("willing_to_relocate", ""),
    "how_did_you_hear": answers.get("how_did_you_hear", ""),
    "right_to_work": answers.get("uk_right_to_work", ""),
}

existing = facts.load(home).get("answers", {})
for key, value in seed.items():
    if value and not existing.get(key):
        existing[key] = value

path = facts.save(home, {"answers": existing, "disclosures": facts.load(home).get("disclosures", {})})
missing = [q.key for q in facts.QUESTIONS if not existing.get(q.key)]
print(f"wrote {len(existing)} answers to {path}")
print("still to answer (run `python -m jobhunter facts`):", ", ".join(missing) or "nothing")
