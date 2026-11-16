"""Turn a shortlisted posting into something you can submit.

Not a form filler. A form filler is the next piece, and it is the one where a
wrong answer is expensive. This does the part that is tedious but safe:

* picks the CV variant the job's own words point at, and the market;
* writes an answer sheet from the answer bank, so the same facts go into every
  form rather than being retyped slightly differently each time;
* pulls the CV evidence that actually matches the posting, so a cover letter or a
  free-text box starts from your own record rather than from invention;
* lists what the posting asks for that the CV does not evidence;
* records the intent in the store, so the pipeline stops being empty.

Nothing is submitted and nothing is invented.
"""

from __future__ import annotations

import re
import sqlite3
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import db, screening

# What each CV variant is for. Scored against the posting's own words; the
# default wins ties, because the asset-management CV is the truthful baseline.
VARIANT_TERMS: dict[str, tuple[str, ...]] = {
    "Risk": (
        "risk", "var", "value at risk", "expected shortfall", "stress test",
        "attribution", "performance analyst", "exposure", "factor model",
        "model risk", "market risk", "scenario",
    ),
    "Credit Risk": (
        "credit", "fixed income", "bond", "issuer", "rating", "spread",
        "default", "covenant", "leverage", "high yield", "investment grade",
    ),
    "Asset Management": (
        "investment management", "asset management", "fund", "portfolio",
        "asset allocation", "equity", "manager research", "wealth",
    ),
}

MARKET_SUFFIX = {"uk": "", "au": " (AU)"}


@dataclass
class Pack:
    job: sqlite3.Row
    variant: str
    market: str
    cv_path: str
    scores: dict[str, int]
    evidence: list[str]
    missing: list[str]
    answers: dict[str, str]
    folder: Path


def choose_variant(title: str, body: str = "") -> tuple[str, dict[str, int]]:
    """Pick a CV variant from the posting's own words.

    The title decides first. A Man Group advert for a *Credit Analyst* mentions
    "asset management" and "portfolio" throughout, because that is what the firm
    does; scoring the whole page let those words outvote "credit". The title is
    the only part of a posting that says what the job is, so it is asked first and
    the body only breaks a tie it cannot.
    """
    title_lower = f" {title.lower()} "
    title_scores = {
        name: sum(title_lower.count(term) for term in terms)
        for name, terms in VARIANT_TERMS.items()
    }
    best_title = max(title_scores, key=lambda name: (title_scores[name], name == "Asset Management"))
    if title_scores[best_title] > 0:
        return best_title, title_scores

    body_lower = f" {body.lower()} "
    scores = {
        name: sum(body_lower.count(term) for term in terms)
        for name, terms in VARIANT_TERMS.items()
    }
    best = max(scores, key=lambda name: (scores[name], name == "Asset Management"))
    if scores[best] == 0:
        best = "Asset Management"
    return best, scores


def cv_file(variant: str, market: str) -> str:
    suffix = "" if variant == "Asset Management" else f" {variant}"
    return f"Jed Dahlke CV{suffix}{MARKET_SUFFIX.get(market, '')}.pdf"


def evidence_from_cv(cv_text: str, posting: str, limit: int = 6) -> list[str]:
    """CV lines that share the most vocabulary with the posting.

    Deliberately mechanical: it surfaces what you already wrote, it does not
    write anything. The output is raw material for a cover letter, with the
    provenance being your own CV.
    """
    posting_words = {word for word in re.findall(r"[a-z]{4,}", posting.lower())}
    if not posting_words:
        return []
    scored: list[tuple[int, str]] = []
    for raw in cv_text.splitlines():
        line = raw.strip(" ·•\t")
        if len(line) < 40:
            continue
        words = set(re.findall(r"[a-z]{4,}", line.lower()))
        overlap = len(words & posting_words)
        if overlap >= 2:
            scored.append((overlap, line))
    scored.sort(key=lambda item: -item[0])
    seen: set[str] = set()
    out: list[str] = []
    for _, line in scored:
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def build(
    connection: sqlite3.Connection,
    job_id: int,
    config: dict,
    *,
    root: Path,
    cv_text: str = "",
) -> Pack:
    job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise SystemExit(f"no job {job_id}")

    posting = f"{job['title']} {job['company']} {job['description']}"
    variant, scores = choose_variant(job["title"], f"{job['company']} {job['description']}")
    market = (job["country"] or "uk").lower()

    report = screening.screen(job["description"] or job["title"], cv_text) if cv_text else None
    missing = [item.lstrip("• ").strip() for item in (report.missing if report else ())]

    answers = {str(k): str(v) for k, v in (config.get("answers") or {}).items()}
    evidence = evidence_from_cv(cv_text, posting) if cv_text else []

    folder = root / "output" / "applications" / _slug(f"{job['company']}-{job['title']}")
    pack = Pack(
        job=job,
        variant=variant,
        market=market,
        cv_path=cv_file(variant, market),
        scores=scores,
        evidence=evidence,
        missing=missing[:8],
        answers=answers,
        folder=folder,
    )
    return pack


def write(pack: Pack, *, root: Path) -> Path:
    pack.folder.mkdir(parents=True, exist_ok=True)
    path = pack.folder / "application.md"
    job = pack.job
    today = datetime.now(UTC).date().isoformat()

    lines = [
        f"# {job['title']}",
        "",
        f"**{job['company']}** — {job['location'] or 'location not stated'}",
        "",
        f"- Posting: {job['url']}",
        f"- Found: {job['source']} on {job['discovered_at'][:10]}",
        f"- Market: {pack.market.upper()}",
        f"- CV to send: `{pack.cv_path}` (in `output/`)",
        f"- Prepared: {today}",
        "",
        "## Answers to paste",
        "",
        "The same facts every time, from `[answers]` in config. Nothing here is",
        "invented at the form.",
        "",
    ]
    for key, value in pack.answers.items():
        lines.append(f"- **{key}**: {value}")

    if pack.evidence:
        lines += [
            "",
            "## Evidence to use",
            "",
            "Lines from your own CV that overlap most with this posting. Use them",
            "for a cover letter or a free-text box; rewrite in your own words.",
            "",
        ]
        lines += [f"- {item}" for item in pack.evidence]

    if pack.missing:
        lines += [
            "",
            "## What the posting asks for that the CV does not evidence",
            "",
            "Decide before you apply: is this a gap you can address, or the reason",
            "to skip it?",
            "",
        ]
        lines += [f"- {item}" for item in pack.missing]

    lines += [
        "",
        "## Checklist",
        "",
        f"- [ ] Upload `{pack.cv_path}`",
        "- [ ] Right to work answered",
        "- [ ] Salary expectation, if asked",
        "- [ ] Cover letter, if required",
        "- [ ] Read the posting once more for a knockout question",
        "- [ ] Submitted, and the confirmation saved to this folder",
        "",
        f"<!-- cv-variant: {pack.variant} | market: {pack.market} | scores: {pack.scores} -->",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def record(connection: sqlite3.Connection, pack: Pack, note_path: str) -> int:
    existing = connection.execute(
        "SELECT id FROM applications WHERE job_id = ? ORDER BY id DESC LIMIT 1", (pack.job["id"],)
    ).fetchone()
    if existing:
        connection.execute(
            "UPDATE applications SET variant = ?, country = ?, updated_at = ? WHERE id = ?",
            (pack.variant, pack.market, db.now(), existing["id"]),
        )
        return int(existing["id"])
    return db.record_application(
        connection,
        job_id=int(pack.job["id"]),
        status="draft",
        approval="pending",
        stage="draft",
        variant=pack.variant,
        country=pack.market,
        note=f"prepared pack: {note_path}",
    )


def prepare(
    store: Path,
    root: Path,
    job_id: int,
    config: dict,
    *,
    cv_text: str = "",
    record_it: bool = True,
    open_it: bool = False,
    verbose: bool = True,
) -> Pack:
    connection = db.connect(store)
    connection.isolation_level = None
    try:
        pack = build(connection, job_id, config, root=root, cv_text=cv_text)
        path = write(pack, root=root)
        if record_it:
            record(connection, pack, str(path))
        if verbose:
            print(f"{pack.job['title']} — {pack.job['company']}")
            print(f"  CV:       {pack.cv_path}")
            print(f"  market:   {pack.market.upper()}")
            print(f"  variant:  {pack.variant}  {pack.scores}")
            print(f"  pack:     {path}")
            if pack.missing:
                print(f"  gaps:     {len(pack.missing)} requirements the CV does not evidence")
        if open_it and pack.job["url"]:
            webbrowser.open(pack.job["url"])
        return pack
    finally:
        connection.close()


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:70] or "application"
