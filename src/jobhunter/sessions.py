"""Capture a signed-in browser session, without ever seeing the password.

Some steps need you to be you: Indeed Easy Apply, LinkedIn, Workday accounts.
Rather than take a password, this opens a normal browser window, lets you sign in
yourself, and saves the resulting storage state — cookies and local storage — to
a file only this user can read.

    python -m jobhunter login --site indeed

Nothing here types a credential, and nothing stores one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SITES = {
    "indeed": "https://uk.indeed.com",
    "linkedin": "https://www.linkedin.com/login",
}


def path_for(site: str, home: Path) -> Path:
    return home / "sessions" / f"{site}.json"


def login(site: str, home: Path) -> int:
    if site not in SITES:
        print(f"unknown site {site!r}; choose from {', '.join(SITES)}")
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install playwright && playwright install chromium")
        return 2

    target = path_for(site, home)
    target.parent.mkdir(parents=True, exist_ok=True)

    print(f"opening {SITES[site]} in a browser window")
    print("sign in there — the password never passes through this tool.")
    print("when you can see your account, come back here and press Enter.\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            locale="en-GB",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto(SITES[site], wait_until="domcontentloaded", timeout=60_000)
        try:
            input("press Enter once you are signed in: ")
        except (EOFError, KeyboardInterrupt):
            print("\ncancelled")
            context.close()
            browser.close()
            return 1
        context.storage_state(path=str(target))
        context.close()
        browser.close()

    # Best effort: make the file private to this account.
    try:
        import os

        os.chmod(target, 0o600)
    except OSError:
        pass

    cookies = 0
    try:
        cookies = len(json.loads(target.read_text(encoding="utf-8")).get("cookies", []))
    except (OSError, ValueError):
        pass
    print(f"saved {cookies} cookies to {target}")
    print("discovery will use it automatically when the file exists.")
    return 0
