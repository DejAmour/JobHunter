"""Find out which applicant tracking system an employer uses, and prove it works.

Guessing board slugs is a losing game: most guesses 404, and a 404 looks the same
as an employer that does not publish a board. So this asks the employer's own
careers page which system it loads, then calls that system's API and counts the
jobs. An entry only reaches ``firms.toml`` if the API answered with at least one
posting.

This is the difference between a registry of 17 employers and one of several
hundred: the costly part was never the code, it was knowing the board slug.
"""

from __future__ import annotations

import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import sources

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
TIMEOUT = 8

CAREER_PATHS = (
    "https://careers.{d}",
    "https://www.{d}/careers",
    "https://{d}",
)

# Systems we can name but not yet read. Recorded so a "no board found" is not
# confused with "uses iCIMS", which is a different job to do later.
UNSUPPORTED = (
    ("icims", re.compile(r"([a-z0-9-]+)\.icims\.com", re.I)),
    ("taleo", re.compile(r"([a-z0-9-]+)\.taleo\.net", re.I)),
    ("successfactors", re.compile(r"([a-z0-9-]+)\.successfactors\.(?:com|eu)", re.I)),
    ("tribepad", re.compile(r"([a-z0-9-]+)\.tribepad\.com", re.I)),
    ("pageup", re.compile(r"([a-z0-9-]+)\.pageuppeople\.com", re.I)),
)

PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I), 1),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I), 1),
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9_-]+)", re.I), 1),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)", re.I), 1),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([a-z0-9_-]+)", re.I), 1),
    ("workday", re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:en-[A-Z]+/)?([A-Za-z0-9_-]+)"), 3),
    (
        "oracle",
        re.compile(
            r"https?://([a-z0-9.-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/"
            r"(?:[a-z]{2}(?:-[A-Z]{2})?/)?sites/([A-Za-z0-9_]+)",
            re.I,
        ),
        2,
    ),
)


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers={"user-agent": USER_AGENT, "accept": "text/html"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read(800_000).decode("utf-8", errors="replace")


def detect(domain: str) -> dict | None:
    """Return a ``firms.toml`` style entry, or None.

    Big employers rarely put the board on the apex domain: BlackRock's careers
    link lives on a subdomain behind a JS app. So this reads the home page,
    follows the first link that looks like careers, and only then decides.
    """
    domain = domain.replace("https://", "").replace("http://", "").strip("/")
    bare = domain[4:] if domain.startswith("www.") else domain
    urls: list[str] = []
    for template in CAREER_PATHS:
        urls.append(template.format(d=bare))

    crawled: set[str] = set()
    for url in urls:
        if url in crawled:
            continue
        crawled.add(url)
        try:
            html = _get(url)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            continue

        # Follow one same-site "careers" link when the page is not the board.
        for follow in _career_links(html, url):
            if follow in crawled:
                continue
            crawled.add(follow)
            try:
                html = _get(follow)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError):
                continue
            found = _match(html)
            if found:
                return found
        found = _match(html)
        if found:
            return found
    return None


def _match(html: str) -> dict | None:
    for ats, pattern, groups in PATTERNS:
        match = pattern.search(html)
        if not match:
            continue
        parts = [g for g in match.groups()[:groups] if g]
        if ats == "workday":
            tenant, wd, site = parts
            return {"name": "", "ats": "workday", "tenant": tenant, "wd": wd, "site": site}
        if ats == "oracle":
            host, site = parts
            return {"name": "", "ats": "oracle", "host": host, "site": site}
        return {"name": "", "ats": ats, "slug": parts[0]}
    for ats, pattern in UNSUPPORTED:
        match = pattern.search(html)
        if match:
            return {"name": "", "ats": ats, "slug": match.groups()[0], "notes": "no adapter yet"}
    return None


def _career_links(html: str, base: str) -> list[str]:
    host = urllib.parse.urlparse(base).netloc
    out: list[str] = []
    for href in re.findall(r'href="([^"]{3,200})"', html):
        if not re.search(r"career|jobs|vacanc", href, re.I):
            continue
        absolute = urllib.parse.urljoin(base, href)
        if urllib.parse.urlparse(absolute).netloc != host:
            continue
        if absolute not in out:
            out.append(absolute)
    return out[:1]


def verify(entry: dict) -> tuple[bool, int, str]:
    """Call the board. Returns (ok, jobs seen, error).

    A 429 is not a "no". The board exists and is throttling us, which after a
    discovery run is the normal state of Workday; dropping the employer because
    of it would silently halve the registry.
    """
    try:
        entry = {**entry, "name": entry.get("name") or "probe"}
        if entry.get("ats") == "workday":
            entry["fast"] = True
        source = sources.build(entry, ["investment"])
        postings = source.list()
    except sources.SourceError as error:
        message = str(error)
        if "429" in message:
            return True, 0, "rate limited, board exists"
        return False, 0, message[:90]
    except Exception as error:  # a broken board is not a crash
        return False, 0, f"{type(error).__name__}: {error}"[:90]
    return bool(postings), len(postings), ""


def run(domains: list[str], *, verbose: bool = True) -> list[dict]:
    """Probe each domain. Returns entries that answered, name filled in."""
    found: list[dict] = []
    for domain in domains:
        domain = domain.strip()
        if not domain or domain.startswith("#"):
            continue
        entry = detect(domain)
        if not entry:
            if verbose:
                print(f"  {domain:<34} no board found", file=sys.stderr)
            continue
        if entry.get("ats") not in sources.KINDS:
            if verbose:
                print(
                    f"  {domain:<34} {entry['ats']:<16} detected, no adapter yet",
                    file=sys.stderr,
                )
            continue
        name = _pretty_name(domain)
        entry["name"] = name
        ok, count, error = verify(entry)
        if not ok:
            if verbose:
                print(f"  {domain:<34} {entry['ats']:<16} detected, no jobs ({error})", file=sys.stderr)
            continue
        if error:
            entry["notes"] = error
        if verbose:
            print(f"  {domain:<34} {entry['ats']:<16} {count if count else '?':>4} jobs")
        found.append(entry)
        time.sleep(1.5)  # the next domain is a different host; still, do not burst
    return found


def _pretty_name(domain: str) -> str:
    host = domain.replace("https://", "").replace("http://", "").split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    stem = host.split(".")[0]
    return stem.replace("-", " ").title()


def as_toml(entry: dict) -> str:
    """One ``[[firms]]`` block, in the same key order as the hand-written file."""
    order = ("name", "ats", "slug", "host", "tenant", "wd", "site", "country", "notes")
    lines = ["[[firms]]"]
    for key in order:
        if key in entry and entry[key] != "":
            lines.append(f'{key} = "{entry[key]}"')
    return "\n".join(lines)


def append_to(path, entries: list[dict]) -> int:
    """Append entries that are not already in the file. Returns how many were added."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    added = 0
    blocks: list[str] = []
    for entry in entries:
        marker = f'name = "{entry["name"]}"'
        if marker in text:
            continue
        blocks.append(as_toml(entry))
        added += 1
    if blocks:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n\n# --- added by probe ---\n\n")
            handle.write("\n\n".join(blocks))
            handle.write("\n")
    return added
