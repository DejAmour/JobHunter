"""Follow an aggregator's "Apply" link to the form that actually has to be filled.

A saved job often points at Adzuna or Reed, which is a listing and not a form.
Filling used to stop there with "no filler for this site", which is true and
useless. This opens the listing, follows the hand-off, and comes back with one
of three facts: the listing has closed, the application happens on a site we can
name, or we could not find the link.

Only used for hosts known to be aggregators. An employer's own page is opened
directly by the filler; nothing here second-guesses it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

#: Hosts whose listing is a hand-off rather than an application form.
AGGREGATORS = (
    "adzuna.",
    "reed.co.uk",
    "indeed.",
    "jooble.",
    "jobijoba.",
    "totaljobs.com",
    "cv-library.co.uk",
    "glassdoor.",
    "efinancialcareers.",
    "linkedin.com/jobs",
)

#: What a closed listing looks like, in the site's own words.
CLOSED = re.compile(
    r"no longer available|no longer accepting|job (?:has )?expired|"
    r"this (?:job|position|vacancy) (?:is )?closed|position has been filled",
    re.I,
)

#: The control that hands off to the application.
APPLY = re.compile(
    r"^(?:apply(?: now)?|apply for (?:this )?(?:job|position|role)|"
    r"i'?m interested|view (?:the )?job|continue to application)$",
    re.I,
)


@dataclass
class Resolution:
    url: str = ""
    final_url: str = ""
    closed: bool = False
    note: str = ""
    title: str = ""

    @property
    def host(self) -> str:
        return urlsplit(self.final_url).netloc.lower()


def looks_like_aggregator(url: str) -> bool:
    lowered = (url or "").lower()
    return any(marker in lowered for marker in AGGREGATORS)


def resolve(url: str, *, headless: bool = True, timeout_ms: int = 45_000, wait_ms: int = 2_000) -> Resolution:
    """Open a listing and follow its Apply control. Never raises."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - environment dependent
        return Resolution(url=url, note="playwright is not installed")

    outcome = Resolution(url=url)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 1100},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(wait_ms)
            outcome.title = page.title()

            text = ""
            try:
                text = page.inner_text("body") or ""
            except Exception:  # noqa: BLE001
                pass
            if CLOSED.search(text):
                outcome.closed = True
                outcome.note = "the listing says the job is no longer available"
                context.close()
                browser.close()
                return outcome

            control = _apply_control(page)
            if control is None:
                outcome.note = "no Apply control on the listing"
                outcome.final_url = page.url
                context.close()
                browser.close()
                return outcome

            before = set(context.pages)
            try:
                control.click(timeout=5_000)
            except Exception:  # noqa: BLE001 - an overlay may swallow it
                href = control.get_attribute("href") or ""
                if href:
                    page.goto(
                        page.evaluate("(href) => new URL(href, location.href).href", href),
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
            page.wait_for_timeout(wait_ms)

            # A hand-off often opens a new tab; the application is in whichever
            # page is now showing it.
            opened = [candidate for candidate in context.pages if candidate not in before]
            if opened:
                page = opened[-1]
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                except Exception:  # noqa: BLE001
                    pass
                page.wait_for_timeout(1_000)

            outcome.final_url = page.url
            try:
                outcome.title = page.title()
            except Exception:  # noqa: BLE001
                pass
            if outcome.final_url == url:
                outcome.note = "the Apply control did not lead anywhere"
            elif CLOSED.search(_body_text(page)):
                outcome.closed = True
                outcome.note = "the destination says the job is no longer available"
            else:
                outcome.note = f"followed Apply to {outcome.host or 'the next page'}"
            context.close()
            browser.close()
    except Exception as error:  # noqa: BLE001 - resolution is best effort
        outcome.note = f"{type(error).__name__}: {error}"[:200]
    return outcome


def _apply_control(page):
    """The most likely hand-off control: a link before a button, first visible."""
    for selector in ("a", "button", "[role='button']", "input[type='submit']"):
        try:
            candidates = page.query_selector_all(selector)
        except Exception:  # noqa: BLE001
            continue
        for element in candidates:
            try:
                text = (element.inner_text() or element.get_attribute("value") or "").strip()
                if not APPLY.match(text):
                    continue
                box = element.bounding_box()
                if box is None or box["width"] == 0:
                    continue
            except Exception:  # noqa: BLE001
                continue
            return element
    return None


def _body_text(page) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:  # noqa: BLE001
        return ""
