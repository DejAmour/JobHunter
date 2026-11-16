"""What does JobSpy actually return for Indeed UK and Australia?"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jobspy import scrape_jobs  # noqa: E402

for country, location in (("UK", "London"), ("Australia", "All Australia")):
    try:
        frame = scrape_jobs(
            site_name=["indeed"],
            search_term="investment analyst",
            location=location,
            country_indeed=country,
            results_wanted=10,
            verbose=0,
        )
        print(f"--- {country}: {len(frame)} rows")
        if not len(frame):
            continue
        row = frame.iloc[0]
        print(f"  title: {row.get('title')}")
        print(f"  job_url: {str(row.get('job_url'))[:80]}")
        print(f"  direct:  {str(row.get('job_url_direct'))[:80]}")
        description = str(row.get("description") or "")
        print(f"  description chars: {len(description)}")
        print(f"  salary: {row.get('min_amount')} - {row.get('max_amount')} {row.get('currency')}")
    except Exception as error:  # noqa: BLE001
        print(f"--- {country} FAILED: {type(error).__name__}: {str(error)[:160]}")
