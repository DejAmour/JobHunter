"""Find postings, throw away the irrelevant ones, screen what is left.

The order matters and is deliberate:

1. **Index only.** Every source can list its board cheaply. Nothing is fetched
   per job until a posting has passed the title and location filters.
2. **Title.** One substring match against the role vocabulary. A firm's board is
   mostly software engineers, compliance and sales; this is the cheapest way to
   cut 95% of it.
3. **Location.** Country inferred from the posting text, Birmingham dropped by
   name, everything unplaceable discarded rather than guessed at.
4. **Company.** The tier-one trading firms are excluded on purpose, so they are
   excluded here rather than by a human remembering.
5. **Screen.** Only now is the description pulled and run through the same
   knockout extraction used before applying.

Anything that survives is written to the store with its decision and coverage,
so the dashboard shows what exists rather than what was remembered.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

from . import db, screening, sources

# Role vocabulary, matched against the *title* only. Deliberately broad: the
# screening step is what rejects a bad fit, and a title filter that is too tight
# silently loses roles nobody ever sees.
TITLE_TERMS: tuple[str, ...] = (
    "investment analyst",
    "investment manager",
    "investment research",
    "investment risk",
    "investment operations",
    "portfolio",
    "asset allocation",
    "fund research",
    "fund analyst",
    "fund manager research",
    "manager research",
    "research analyst",
    "equity research",
    "credit analyst",
    "credit research",
    "fixed income",
    "performance analyst",
    "performance and attribution",
    "attribution",
    "risk analyst",
    "market risk",
    "model risk",
    "quantitative analyst",
    "quantitative research",
    "trading",
    "trade support",
    "product specialist",
    "client reporting",
    "graduate",
    "internship",
    "placement",
    "summer analyst",
)

# Words that mark a posting as beyond an early-career applicant. Kept as a flag
# rather than a filter: an "Investment Analyst" at a small firm can be senior,
# and a "Senior Analyst" at a large one can be a first job.
SENIOR_TERMS = (
    "head of",
    "director",
    "managing director",
    "chief",
    "principal",
    "vice president",
    "vp ",
    "svp",
    "evp",
    "senior",
    "sr ",
    "sr.",
    "partner",
    "team lead",
)


def fingerprint(company: str, title: str, location: str) -> str:
    """Identity of a posting as a human would see it.

    Aggregators carry the same advert three times with three redirect URLs, so
    the URL cannot be the identity. Employer + title + place can be.
    """
    raw = f"{company}|{title}|{location}".lower()
    return re.sub(r"[^a-z0-9]+", "", raw)[:160]

UK_WORDS = (
    "united kingdom", "england", "scotland", "wales", "northern ireland",
    "london", "edinburgh", "glasgow", "manchester", "leeds", "bristol", "exeter",
    "cardiff", "belfast", "reading", "guildford", "oxford", "cambridge",
    "nottingham", "sheffield", "newcastle", "liverpool", "southampton",
    "brighton", "milton keynes", "slough", "watford", "croydon", "surrey",
    "kent", "essex", "hertfordshire", "berkshire", "devon", "somerset",
    "city of london", "canary wharf", "poole", "bournemouth", "leicester",
    "coventry", "derby", "swindon", "cheltenham", "gloucester", "taunton",
    "plymouth", "truro", "chester", "warrington", "preston", "york", "hull",
    "norwich", "ipswich", "luton", "st albans", "woking", "kingston",
)

AU_WORDS = (
    "australia", "sydney", "melbourne", "brisbane", "perth", "adelaide",
    "canberra", "hobart", "darwin", "gold coast", "new south wales",
    "victoria", "queensland", "western australia", "south australia",
    "tasmania", "nsw", "qld", "vic", "wa", "sa", "act",
)

# Places that contain a UK-sounding word but are not in the UK. "New York"
# matched "York" until this list existed, which quietly offered US roles as
# London ones. Checked per location fragment, before UK_WORDS.
NON_UK_WORDS = (
    "united states", "usa", "new york", "boston", "massachusetts", "texas",
    "fort worth", "chicago", "san francisco", "palo alto", "new jersey",
    "jersey city", "philadelphia", "atlanta", "charlotte", "toronto",
    "montreal", "hong kong", "singapore", "shanghai", "beijing", "shenzhen",
    "tokyo", "mumbai", "bangalore", "bengaluru", "gurgaon", "delhi", "pune",
    "hyderabad", "chennai", "sofia", "warsaw", "krakow", "bucharest",
    "budapest", "prague", "frankfurt", "munich", "berlin", "paris", "madrid",
    "milan", "zurich", "geneva", "luxembourg", "dublin", "amsterdam",
    "stockholm", "copenhagen", "oslo", "helsinki", "dubai", "abu dhabi",
    "riyadh", "doha", "johannesburg", "cape town", "sao paulo", "seoul",
    "taipei", "kuala lumpur", "jakarta", "manila", "bangkok",
    "poland", "india", "china", "japan", "germany", "france", "spain", "italy",
    "switzerland", "netherlands", "ireland", "portugal", "belgium", "austria",
    "sweden", "norway", "denmark", "finland", "greece", "turkey", "israel",
    "canada", "brazil", "mexico", "south korea", "taiwan", "malaysia",
    "indonesia", "philippines", "thailand", "vietnam", "egypt", "nigeria",
    "kenya", "argentina", "chile", "colombia", "peru", "romania", "hungary",
    "czech", "slovakia", "croatia", "serbia", "bulgaria", "estonia", "latvia",
    "lithuania", "ukraine", "russia", "south africa",
)


SEEK_SECTORS = (
    "banking", "insurance", "government", "consulting",
    "funds management", "superannuation", "financial", "analyst",
)

# Workday boards are searched by term server-side, so each term costs a request.
# This shorter list is what gets sent; the full TITLE_TERMS list is applied
# locally afterwards either way.
WORKDAY_TERMS: tuple[str, ...] = (
    "investment",
    "portfolio",
    "risk",
    "analyst",
)


@dataclass
class Filters:
    title_terms: tuple[str, ...] = TITLE_TERMS
    exclude_locations: tuple[str, ...] = ()
    exclude_companies: tuple[str, ...] = ()
    allow_countries: tuple[str, ...] = ("uk", "au")

    @classmethod
    def from_config(cls, config: dict) -> Filters:
        search = config.get("search", {})
        raw = [str(c).lower() for c in search.get("countries", ["GB", "AU"])]
        countries = tuple(
            {"gb": "uk", "uk": "uk", "aus": "au"}.get(code, code) for code in raw
        )
        return cls(
            title_terms=tuple(search.get("title_terms") or TITLE_TERMS),
            exclude_locations=tuple(search.get("exclude_locations", [])),
            exclude_companies=tuple(search.get("exclude_companies", [])),
            allow_countries=countries,
        )


# A posting that clears the knockouts but evidences under a quarter of its own
# requirements is shown, not recommended. 0.25 is calibrated against the graded
# coverage measure in screening._coverage, not the older binary one.
MIN_COVERAGE = 0.25


@dataclass
class Result:
    firm: str
    seen: int = 0
    after_title: int = 0
    after_location: int = 0
    kept: int = 0
    stored: int = 0
    error: str = ""
    kept_titles: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def title_matches(title: str, terms: tuple[str, ...]) -> bool:
    flat = f" {_norm(title)} "
    return any(f" {_norm(term)}" in flat for term in terms if term.strip())


def is_senior(title: str) -> bool:
    flat = f" {_norm(title)} "
    return any(f" {_norm(term).strip()} " in flat or flat.startswith(f" {_norm(term).strip()} ") for term in SENIOR_TERMS)


def country_of(location: str, hint: str = "") -> str:
    """uk, au, or '' when the posting does not say.

    An empty answer is a rejection, not a guess: a job in Bangalore is not a
    near-miss, and neither is one in Jersey City.

    Multi-city strings ("London; New York") are split and judged per fragment,
    so a role open in both places counts as a UK role rather than disappearing.
    """
    if hint:
        return hint.lower()
    for part in re.split(r"[;/|]", location):
        text = f" {_norm(part)} "
        if not text.strip():
            continue
        if any(f" {_norm(word)} " in text for word in NON_UK_WORDS):
            continue
        if any(f" {_norm(word)} " in text for word in UK_WORDS):
            return "uk"
        if any(f" {_norm(word)} " in text for word in AU_WORDS):
            return "au"
    return ""


def is_excluded_location(location: str, filters: Filters) -> bool:
    text = _norm(location)
    return any(_norm(word) in text for word in filters.exclude_locations if word.strip())


def is_excluded_company(company: str, filters: Filters) -> bool:
    text = _norm(company)
    return any(_norm(word) in text for word in filters.exclude_companies if word.strip())


def run(
    config: dict,
    firms: list[dict],
    *,
    store,
    cv_text: str = "",
    candidate: screening.Candidate | None = None,
    dry_run: bool = False,
    only: str = "",
    only_ats: str = "",
    max_detail: int = 25,
    verbose: bool = True,
) -> list[Result]:
    filters = Filters.from_config(config)
    results: list[Result] = []

    selected = [
        f
        for f in firms
        if f.get("enabled", True) is not False
        and (not only or only.lower() in str(f.get("name", "")).lower())
        and (not only_ats or str(f.get("ats", "")).lower() == only_ats.lower())
    ]

    # Fingerprints already in the store, so an advert seen on three aggregators
    # is stored once even across runs.
    seen_fingerprints: set[str] = set()
    if not dry_run:
        probe_connection = db.connect(store)
        try:
            for row in probe_connection.execute("SELECT company, title, location FROM jobs"):
                seen_fingerprints.add(fingerprint(row["company"], row["title"], row["location"]))
        finally:
            probe_connection.close()
    for entry in selected:
        name = str(entry.get("name", "?"))
        result = Result(firm=name)
        results.append(result)

        try:
            aggregated = dict(entry)
            aggregator = dict(config.get("aggregators", {}))
            # Environment wins so keys can stay out of the config file entirely.
            for env, key in (
                ("JOBHUNTER_ADZUNA_ID", "adzuna_app_id"),
                ("JOBHUNTER_ADZUNA_KEY", "adzuna_app_key"),
                ("JOBHUNTER_REED_KEY", "reed_api_key"),
            ):
                if os.environ.get(env):
                    aggregator[key] = os.environ[env]
            if aggregated.get("ats") == "adzuna":
                aggregated["app_id"] = aggregated.get("app_id") or aggregator.get("adzuna_app_id", "")
                aggregated["app_key"] = aggregated.get("app_key") or aggregator.get("adzuna_app_key", "")
            elif aggregated.get("ats") == "reed":
                aggregated["api_key"] = aggregated.get("api_key") or aggregator.get("reed_api_key", "")
            source = sources.build(aggregated, list(WORKDAY_TERMS))
            postings = source.list()
        except sources.SourceError as error:
            result.error = str(error)
            if verbose:
                print(f"  ! {name}: {error}", file=sys.stderr)
            continue
        except Exception as error:  # a broken board must not stop the run
            result.error = f"{type(error).__name__}: {error}"
            if verbose:
                print(f"  ! {name}: {result.error}", file=sys.stderr)
            continue

        result.seen = len(postings)
        kept = []
        for posting in postings:
            if not posting.url or not posting.title:
                continue
            if not title_matches(posting.title, filters.title_terms):
                continue
            result.after_title += 1
            if is_excluded_company(posting.company or name, filters):
                continue
            country = country_of(posting.location, posting.country)
            if country not in filters.allow_countries:
                continue
            if is_excluded_location(posting.location, filters):
                continue
            if posting.source == "seek":
                sector = (posting.department or "").lower()
                if not any(word in sector for word in SEEK_SECTORS):
                    continue
            result.after_location += 1
            mark = fingerprint(posting.company or name, posting.title, posting.location)
            if mark in seen_fingerprints:
                continue
            kept.append((posting, country, mark))

        result.kept = len(kept)
        if verbose:
            print(f"  {name}: {result.seen} listed, {result.after_title} match title, {result.kept} in scope")

        details = 0
        connection = db.connect(store) if not dry_run else None
        if connection is not None:
            # Autocommit. A Workday board can take minutes to page through, and a
            # firm-wide transaction would hold the write lock for that whole time,
            # blocking the dashboard. One job, one transaction.
            connection.isolation_level = None
        try:
            for posting, country, mark in kept:
                description = posting.description
                if posting.salary:
                    # Put the advertised range where the screening regexes look.
                    description = f"Salary: {posting.salary}\n\n{description}".strip()
                if len(description) < 400 and details < max_detail:
                    try:
                        description = source.detail(posting) or description
                        details += 1
                    except Exception:
                        pass

                report = screening.screen(description or posting.title, cv_text, candidate)
                decision = report.decision
                note = ""
                # Some boards (SEEK) only give a teaser, and the full page sits
                # behind a bot wall. An unreadable posting is not an approving one,
                # so it is marked "ask" and linked rather than passed as "apply".
                if decision == "apply" and len(description) < 400:
                    decision = "ask"
                    note = "short listing text only; open the posting before applying"
                # A head-of or director title is not a graduate application even
                # when the keywords line up; it is shown, not recommended.
                if decision == "apply" and is_senior(posting.title):
                    decision = "ask"
                    note = "senior title; check the level before applying"
                # Zero overlap with the CV is not a recommendation, whatever the
                # knockouts said.
                if decision == "apply" and report.coverage is not None and report.coverage < MIN_COVERAGE:
                    decision = "ask"
                    note = "low keyword overlap with the CV; read the posting"
                result.kept_titles.append(
                    f"[{decision}|{country}] {posting.title} — {posting.company or name}"
                )
                if dry_run or connection is None:
                    continue
                db.upsert_job(
                    connection,
                    url=posting.url,
                    company=posting.company or name,
                    title=posting.title,
                    location=posting.location,
                    source=f"{posting.source}:{name}",
                    description=description,
                    posted_at=posting.posted_at,
                    decision=decision,
                    coverage=report.coverage,
                    country=country,
                    department=posting.department,
                    seniority="senior" if is_senior(posting.title) else "",
                )
                if note:
                    connection.execute("UPDATE jobs SET notes = ? WHERE url = ?", (note, posting.url))
                seen_fingerprints.add(mark)
                result.stored += 1
            if connection is not None and connection.isolation_level is not None:
                connection.commit()
        finally:
            if connection is not None:
                connection.close()

    return results


def summary(results: list[Result]) -> str:
    ok = [r for r in results if not r.error]
    failed = [r for r in results if r.error]
    stored = sum(r.stored for r in results)
    lines = [
        f"firms:   {len(results)} ({len(ok)} read, {len(failed)} failed)",
        f"listed:  {sum(r.seen for r in results)} postings",
        f"in scope: {sum(r.kept for r in results)}",
        f"saved:   {stored}",
    ]
    if failed:
        lines.append("failed:  " + "; ".join(f"{r.firm} ({r.error[:60]})" for r in failed))
    return "\n".join(lines)
