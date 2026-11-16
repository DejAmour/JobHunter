"""Which location strings does JobSpy accept for Australia?"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jobspy import scrape_jobs  # noqa: E402

for location in ("Australia", "Melbourne", "Sydney", "Melbourne VIC"):
    try:
        frame = scrape_jobs(
            site_name=["indeed"],
            search_term="investment analyst",
            location=location,
            country_indeed="Australia",
            results_wanted=10,
            verbose=0,
        )
        first = frame.iloc[0].get("title") if len(frame) else ""
        print(f"{location:<16} rows={len(frame):<3} {first}")
    except Exception as error:  # noqa: BLE001
        print(f"{location:<16} FAILED {type(error).__name__}: {str(error)[:100]}")
