"""One-off: can the saved-page parser read a live public search page?

Not a collector. This answers "would Ctrl+S then import work?" empirically, once.
"""

from __future__ import annotations

import sys
import urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from jobhunter import importer  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TARGETS = [
    ("linkedin", "https://www.linkedin.com/jobs/search?keywords=investment%20analyst&location=London"),
    ("indeed", "https://uk.indeed.com/jobs?q=investment+analyst&l=London"),
]

for kind, url in TARGETS:
    request = urllib.request.Request(
        url,
        headers={
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120 Safari/537.36",
            "accept-language": "en-GB,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            html = response.read().decode("utf-8", errors="replace")
        status = response.status
    except Exception as error:  # noqa: BLE001
        print(f"{kind}: {type(error).__name__} {error}")
        continue

    found = importer.parse(html, kind)
    print(f"{kind}: HTTP {status}, {len(html)} chars, {len(found)} cards parsed")
    for item in found[:3]:
        print(f"   {item.title} | {item.company} | {item.location}")
