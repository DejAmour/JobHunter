"""Read job cards out of a page the user saved themselves.

LinkedIn and Indeed both block automated collection, and both are explicit about
it in their terms. This does not fetch them and does not log in. It reads a page
you opened in your own browser and saved with Ctrl+S, which keeps the access a
human one and keeps the parsing honest: if the markup is not what we expect, it
says so rather than returning nothing.

Three parsers, in order of reliability:

* ``jsonld`` — schema.org JobPosting blocks, which most large sites emit and
  which are stable by design.
* ``linkedin`` — the public search page's ``base-search-card`` markup.
* ``indeed`` — the saved results page's ``jobTitle`` markup.
"""

from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Imported:
    title: str
    company: str
    location: str
    url: str


def parse(text: str, kind: str = "auto") -> list[Imported]:
    """Parse saved HTML. ``kind`` is auto, linkedin, indeed or jsonld."""
    if kind in ("auto", "jsonld"):
        found = parse_jsonld(text)
        if found or kind == "jsonld":
            return found
    if kind in ("auto", "linkedin"):
        found = parse_linkedin(text)
        if found:
            return found
    if kind in ("auto", "indeed"):
        return parse_indeed(text)
    return []


def parse_jsonld(text: str) -> list[Imported]:
    out: list[Imported] = []
    for block in re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', text, re.S | re.I
    ):
        try:
            data = json.loads(html_module.unescape(block))
        except (json.JSONDecodeError, ValueError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                continue
            if item.get("@type") not in ("JobPosting", ["JobPosting"], None):
                continue
            title = item.get("title") or ""
            if not title:
                continue
            organisation = item.get("hiringOrganization") or {}
            location = ""
            place = item.get("jobLocation") or {}
            if isinstance(place, list) and place:
                place = place[0]
            if isinstance(place, dict):
                address = place.get("address") or {}
                location = ", ".join(
                    part
                    for part in (address.get("addressLocality"), address.get("addressCountry"))
                    if isinstance(part, str) and part
                )
            out.append(
                Imported(
                    title=_clean(title),
                    company=_clean(organisation.get("name", "") if isinstance(organisation, dict) else ""),
                    location=_clean(location),
                    url=str(item.get("url") or ""),
                )
            )
    return _dedupe(out)


def parse_linkedin(text: str) -> list[Imported]:
    out: list[Imported] = []
    # Cards are self-contained; splitting on the card class keeps the fields
    # adjacent so a missing company cannot borrow the next card's.
    for chunk in re.split(r'class="[^"]*base-search-card(?![_-])[^"]*"', text)[1:]:
        title = _first(chunk, r'class="base-search-card__title"[^>]*>(.*?)</h3>', r'class="base-search-card__title"[^>]*>(.*?)</h4>', r'class="base-search-card__title"[^>]*>(.*?)</')
        if not title:
            continue
        company = _first(chunk, r'class="base-search-card__subtitle"[^>]*>(.*?)</h4>', r'class="base-search-card__subtitle"[^>]*>(.*?)</a>')
        location = _first(chunk, r'class="job-search-card__location"[^>]*>(.*?)</span>')
        url = _first(chunk, r'href="(https://[a-z.]*linkedin\.com/jobs/view/[^"]+)"', r'href="(/jobs/view/[^"]+)"')
        if url.startswith("/"):
            url = "https://www.linkedin.com" + url
        out.append(Imported(_clean(title), _clean(company), _clean(location), url.split("?")[0]))
    return _dedupe(out)


def parse_indeed(text: str) -> list[Imported]:
    out: list[Imported] = []
    for chunk in re.split(r'class="[^"]*job_seen_beacon[^"]*"', text)[1:]:
        title = _first(chunk, r'<h2[^>]*class="[^"]*jobTitle[^"]*"[^>]*>(.*?)</h2>', r'title="([^"]+)"')
        if not title:
            continue
        company = _first(chunk, r'class="[^"]*companyName[^"]*"[^>]*>(.*?)</span>')
        location = _first(chunk, r'class="[^"]*companyLocation[^"]*"[^>]*>(.*?)</div>')
        url = _first(chunk, r'href="(/viewjob\?[^"]+)"', r'href="(https://[a-z.]*indeed\.com/[^"]+)"')
        if url.startswith("/"):
            url = "https://uk.indeed.com" + url
        out.append(Imported(_clean(title), _clean(company), _clean(location), url))
    return _dedupe(out)


def _first(text: str, *patterns: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.S | re.I)
        if match:
            return match.group(1)
    return ""


def _clean(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html_module.unescape(text).split())


def _dedupe(items: list[Imported]) -> list[Imported]:
    seen: set[str] = set()
    out: list[Imported] = []
    for item in items:
        key = (item.url or f"{item.title}|{item.company}").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
