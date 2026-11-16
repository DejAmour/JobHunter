"""One-off: does the Indeed browser source actually return postings?

Runs one keyword, one page, prints what it found and one description length.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobhunter.sources import IndeedBrowser  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

source = IndeedBrowser(
    company="Indeed UK",
    country="uk",
    keywords=["investment analyst"],
    where="London",
    pages=1,
    headless=True,
    max_jobs=12,
)
postings = source.list()
print(f"{len(postings)} postings")
for posting in postings[:12]:
    print(f"  {posting.title[:56]:<56} {posting.company[:24]:<24} {posting.location[:18]}")

if postings:
    text = source.detail(postings[0])
    print(f"\ndescription for first: {len(text)} chars")
    print(text[:300].replace("\n", " "))
