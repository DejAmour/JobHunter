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


def login_workday(home: Path, config: dict, account, *, url: str = "", create: bool = False) -> int:
    """Sign in to one employer's Workday tenant and keep the cookies.

    The attended version of what ``fill`` does headless: a real window, the
    password from the Credential Manager, and a pause for anything only a human
    can do — a verification code when no mailbox is configured, or a captcha.
    The saved session makes the next application at that employer one page
    shorter.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install playwright && playwright install chromium")
        return 2

    from . import accounts as accounts_module
    from . import fill as form_fill
    from . import mailbox

    password = accounts_module.password(account)
    if not password:
        print(f"no password stored for {account.firm}")
        print(f'run `jobhunter account add --firm "{account.firm}"` first')
        return 2

    target = accounts_module.session_path(home, account)
    target.parent.mkdir(parents=True, exist_ok=True)

    on_code = None
    if mailbox.available(config):
        from datetime import datetime

        started = datetime.now()

        def on_code():
            found = mailbox.latest_code(config, since=started, wait_seconds=120)
            return found.value if found else ""

    # No saved session means no account here yet, so create rather than guess.
    create = create or not target.is_file()

    print(f"{account.firm}: {account.email}")
    print("creating the account" if create else "signing in")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            locale="en-GB",
            viewport={"width": 1280, "height": 900},
            storage_state=str(target) if target.is_file() else None,
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(1500)
            workday = form_fill.Workday()
            workday.start(page)
            status = workday.sign_in(
                page, email=account.email, password=password, create=create, on_code=on_code
            )
            print(f"  {status}")

            # An account may already exist from an earlier attempt, or may not
            # exist at all; that is a door problem, not a failure. Try the other
            # door once rather than making a person guess with --create. A wrong
            # password is not a door problem, so it is not retried.
            if status.startswith("failed: an account already exists"):
                status = workday.sign_in(
                    page, email=account.email, password=password, create=False, on_code=on_code
                )
                print(f"  {status}")
            elif status == "bad-password" or status.startswith("failed: no sign-in"):
                pass
            elif status.startswith("failed:") and create is False:
                print("  no such account here; creating it")
                status = workday.sign_in(
                    page, email=account.email, password=password, create=True, on_code=on_code
                )
                print(f"  {status}")
                if status.startswith("failed: an account already exists"):
                    status = workday.sign_in(
                        page, email=account.email, password=password, create=False, on_code=on_code
                    )
                    print(f"  {status}")

            if status == "code-needed":
                print("\nWorkday wants to verify your email — a code, and sometimes a link.")
                print("Finish it in the browser window; this waits until you are through.")
                try:
                    input("press Enter once you are signed in: ")
                except (EOFError, KeyboardInterrupt):
                    print("\ncancelled")
                    return 1
                status = (
                    "signed-in"
                    if workday.step(page) not in ("create-account", "sign-in")
                    else "still on the account form"
                )

            if status not in ("signed-in", "created"):
                print(f"  did not get through: {status}")
                if status == "bad-password":
                    print(f'  refresh it: `jobhunter account add --firm "{account.firm}"`')
                page.screenshot(path=str(target.with_suffix(".png")), full_page=True)
                print(f"  screenshot: {target.with_suffix('.png')}")
                return 1

            tenant = accounts_module.tenant_from_url(page.url)
            if tenant:
                accounts_module.upsert_tenant(home, account.firm, tenant)
            context.storage_state(path=str(target))
            print(f"  session saved: {target.name}")
            print("  the next `fill` at this employer skips the sign-in.")
            return 0
        except Exception as error:  # noqa: BLE001
            # A traceback is useless here: the useful part is the screenshot and
            # the one line Playwright complained about.
            first = (str(error).strip().splitlines() or [""])[0][:220]
            print(f"\nstopped: {type(error).__name__}: {first}")
            try:
                shot = target.with_suffix(".png")
                page.screenshot(path=str(shot), full_page=True)
                print(f"  screenshot: {shot}")
            except Exception:  # noqa: BLE001
                pass
            return 1
        finally:
            context.close()
            browser.close()

