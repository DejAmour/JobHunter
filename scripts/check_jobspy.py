"""Does JobSpy reach Indeed from here?"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jobspy import scrape_jobs  # noqa: E402

try:
    frame = scrape_jobs(
        site_name=["indeed"],
        search_term="investment analyst",
        location="London",
        country_indeed="UK",
        results_wanted=15,
        verbose=0,
    )
    print(f"rows: {len(frame)}")
    if len(frame):
        print("columns:", ", ".join(frame.columns[:12]))
        for _, row in frame.head(8).iterrows():
            print(f"  {str(row.get('title'))[:52]:<52} {str(row.get('company'))[:22]:<22} {str(row.get('location'))[:20]}")
except Exception as error:  # noqa: BLE001
    print(f"FAILED: {type(error).__name__}: {str(error)[:300]}")
