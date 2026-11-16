"""Read a page you saved yourself (LinkedIn, Indeed) into the store.

    python -m jobhunter import --file saved.html
    python -m jobhunter import --file saved.html --kind indeed
    python -m jobhunter import --file saved.html --dry-run

The point is that nothing is fetched: you open the page in your browser, save it,
and this reads the file. No login, no automation against a site that forbids it.
"""

from __future__ import annotations

import sys

from . import cv as cv_module
from . import db, importer, screening, sources


def run(
    config: dict,
    path: str,
    *,
    store,
    kind: str = "auto",
    dry_run: bool = False,
    cv_text: str = "",
    candidate: screening.Candidate | None = None,
) -> int:
    from . import discover

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    found = importer.parse(text, kind)
    if not found:
        print(
            "no job cards found. Save the page with Ctrl+S (complete page), then try\n"
            "  --kind linkedin, --kind indeed or --kind jsonld",
            file=sys.stderr,
        )
        return 2

    print(f"{len(found)} cards read from {path}")
    stored = 0
    connection = None if dry_run else db.connect(store)
    if connection is not None:
        connection.isolation_level = None

    try:
        for item in found:
            country = discover.country_of(item.location)
            if country not in ("uk", "au"):
                print(f"  skipped (not UK or AU): {item.title} — {item.location}")
                continue
            if discover.is_excluded_location(item.location, discover.Filters.from_config(config)):
                print(f"  skipped (excluded location): {item.title} — {item.location}")
                continue
            # A saved card has a title and a company, not a description, so no
            # honest screening is possible; it lands as "ask" for a human to read.
            decision = "ask"
            print(f"  {item.title} — {item.company} ({item.location})")
            if connection is None:
                continue
            db.upsert_job(
                connection,
                url=item.url or f"import:{item.title}:{item.company}",
                company=item.company,
                title=item.title,
                location=item.location,
                source=f"import:{kind if kind != 'auto' else 'saved page'}",
                description="",
                decision=decision,
                country=country,
            )
            connection.execute(
                "UPDATE jobs SET notes = ? WHERE url = ?",
                ("imported from a saved page; read the posting before applying", item.url),
            )
            stored += 1
    finally:
        if connection is not None:
            connection.close()

    print(f"saved {stored}")
    return 0
