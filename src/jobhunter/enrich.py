"""Fetch the real posting text for rows that only stored a summary.

Aggregators truncate: Adzuna gives the first few hundred characters, Reed gives
a teaser, SEEK gives one line. That is enough to spot a title, not enough to
screen, so those rows sit at "apply" with no coverage — the weakest evidence in
the store.

This opens each posting in a real browser (many are JavaScript applications,
Workday especially), takes the main text, re-screens it, and rewrites the row.
A row that still cannot be read is marked `ask`, because an unreadable posting is
not an approved one.

    python -m jobhunter refresh --limit 40
    python -m jobhunter refresh --decision apply --country uk
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

from . import cv as cv_module
from . import db, screening

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Most specific first. Workday, Indeed and the common CMSs all hide descriptions
# somewhere in here; the fallback is the whole body, which is noisy but honest.
EXTRACT = """
() => {
  const selectors = [
    '[data-automation-id="jobPostingDescription"]',
    '#jobDescriptionText',
    '[data-testid="jobsearch-JobComponent-description"]',
    'article',
    'main',
    '[class*="job-description"]',
    '[class*="jobDescription"]',
    '[id*="description"]'
  ];
  for (const selector of selectors) {
    const node = document.querySelector(selector);
    if (node && node.innerText && node.innerText.length > 250) return node.innerText;
  }
  return document.body ? document.body.innerText : '';
}
"""


@dataclass
class Refreshed:
    job_id: int
    company: str
    title: str
    chars: int
    decision: str
    coverage: float | None
    error: str = ""


def run(
    config: dict,
    *,
    store,
    limit: int = 40,
    decision: str = "apply",
    country: str = "",
    selection_chars: int = 1200,
    readable_chars: int = 400,
    dry_run: bool = False,
    verbose: bool = True,
) -> list[Refreshed]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install playwright && playwright install chromium")
        return []

    # Adzuna truncates at 500 characters and Reed at about 450, so "short" has to
    # mean "shorter than a real posting", not "shorter than a paragraph".
    where = ["(LENGTH(description) < ? OR coverage IS NULL)"]
    params: list = [selection_chars]
    if decision:
        where.append("decision = ?")
        params.append(decision)
    if country:
        where.append("country = ?")
        params.append(country)

    connection = db.connect(store)
    connection.isolation_level = None
    rows = connection.execute(
        f"SELECT * FROM jobs WHERE {' AND '.join(where)} "
        "ORDER BY CASE decision WHEN 'apply' THEN 0 ELSE 1 END, discovered_at DESC LIMIT ?",
        [*params, limit],
    ).fetchall()

    if not rows:
        print("nothing needs refreshing")
        connection.close()
        return []

    cv_path = config.get("profile", {}).get("cv", "")
    cv_text = ""
    from pathlib import Path

    if cv_path and Path(cv_path).is_file():
        cv_text = cv_module.extract(Path(cv_path))[0]
    candidate = _candidate(config)

    print(f"{len(rows)} postings to read")
    results: list[Refreshed] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(user_agent=UA, locale="en-GB", viewport={"width": 1280, "height": 900})
        page = context.new_page()

        for row in rows:
            result = Refreshed(row["id"], row["company"], row["title"], len(row["description"]), row["decision"], row["coverage"])
            results.append(result)
            try:
                page.goto(row["url"], wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(1200)
                text = page.evaluate(EXTRACT) or ""
            except Exception as error:  # noqa: BLE001 - one bad page must not stop the run
                result.error = f"{type(error).__name__}: {str(error)[:60]}"
                if verbose:
                    print(f"  ! {row['company'][:22]:<22} {result.error}")
                time.sleep(1.0)
                continue

            text = _clean(text)
            result.chars = len(text)

            if len(text) < readable_chars:
                result.error = "no readable text"
                if not dry_run:
                    connection.execute(
                        "UPDATE jobs SET decision = 'ask', notes = ? WHERE id = ? AND decision = 'apply'",
                        ("could not read the posting; open it before applying", row["id"]),
                    )
                if verbose:
                    print(f"  - {row['company'][:22]:<22} unreadable")
                time.sleep(1.0)
                continue

            report = screening.screen(text, cv_text, candidate)
            result.decision = report.decision
            result.coverage = report.coverage
            if not dry_run:
                connection.execute(
                    "UPDATE jobs SET description = ?, decision = ?, coverage = ?, notes = ? WHERE id = ?",
                    (text[:60_000], report.decision, report.coverage, "", row["id"]),
                )
            if verbose:
                coverage = f"{report.coverage:.0%}" if report.coverage is not None else "  -"
                print(f"  {coverage:>4} {report.decision:<7} {row['company'][:22]:<22} {row['title'][:44]}")

            time.sleep(1.2)

        context.close()
        browser.close()

    connection.close()
    return results


def _candidate(config: dict) -> screening.Candidate:
    block = config.get("candidate", {})
    return screening.Candidate(
        needs_sponsorship=bool(block.get("needs_sponsorship", False)),
        salary_floor=int(block["salary_floor"]) if block.get("salary_floor") is not None else None,
        currency=str(block.get("currency", "GBP")),
        remote_only=bool(block.get("remote_only", False)),
        years_experience=float(block["years_experience"]) if block.get("years_experience") is not None else None,
        clearances=tuple(block.get("clearances", [])),
        right_to_work=block.get("right_to_work"),
    )


def _clean(text: str) -> str:
    import re

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def summary(results: list[Refreshed]) -> str:
    read = [r for r in results if not r.error and r.chars >= 400]
    scored = [r for r in read if r.coverage is not None]
    improved = [r for r in read if r.coverage is not None and r.coverage > 0.2]
    return (
        f"attempted: {len(results)}\n"
        f"read:      {len(read)}\n"
        f"scored:    {len(scored)}\n"
        f"above 20%: {len(improved)}"
    )
