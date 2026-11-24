"""Job board adapters.

Every source here reads a *public* endpoint that the employer's own careers page
loads. No scraping of rendered pages, no logins, no headless browser: if a firm
hides its jobs behind a login or a bot wall, it is simply not a source.

Two calls per source, deliberately separated:

* ``list()`` returns the index — cheap, no job descriptions.
* ``detail()`` fetches one description.

Discovery filters on the index (title, location, company) and only then pays for
descriptions, which is what keeps a run to tens of requests rather than
thousands.

The shape of each API differs enough that a shared abstraction would hide the
differences rather than remove them, so each source is written plainly and
returns the same ``Posting``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from dataclasses import dataclass
from typing import Any

USER_AGENT = "jobhunter/0.1 (personal job search; +https://github.com/DejAmour/JobHunter)"
TIMEOUT = 25
PAUSE = 0.4  # between requests to the same host; these are free services


@dataclass(frozen=True)
class Posting:
    company: str
    title: str
    location: str
    url: str
    source: str
    description: str = ""
    department: str = ""
    posted_at: str | None = None
    country: str = ""
    salary: str = ""


class SourceError(RuntimeError):
    pass


def _request(url: str, *, body: dict | None = None, retries: int = 4) -> str:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"user-agent": USER_AGENT, "accept": "application/json"}
    if data is not None:
        headers["content-type"] = "application/json"

    last: Exception | None = None
    for attempt in range(retries):
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                # Workday answers 429 when a run is too eager; it also tells you how
                # long to wait. Believing it is cheaper than being locked out.
                wait = _retry_after(error) or (5 * (3**attempt))
                time.sleep(min(wait, 60))
                last = error
                continue
            raise SourceError(f"{url} -> HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise SourceError(f"{url} -> {error}") from error
    raise SourceError(f"{url} -> {last}")


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    try:
        value = error.headers.get("Retry-After")
        return float(value) if value else None
    except (AttributeError, TypeError, ValueError):
        return None


def _json(url: str, *, body: dict | None = None, retries: int = 4) -> Any:
    return json.loads(_request(url, body=body, retries=retries))


class Source:
    """Base class: subclasses fill in ``name`` and implement the two calls."""

    name = "base"

    def __init__(self, company: str = "", **options: Any) -> None:
        self.company = company
        self.options = options

    def list(self) -> list[Posting]:  # pragma: no cover - interface
        raise NotImplementedError

    def detail(self, posting: Posting) -> str:
        """Most boards ship the description with the index."""
        return posting.description


class Greenhouse(Source):
    name = "greenhouse"

    def __init__(self, company: str = "", slug: str = "", **options: Any) -> None:
        super().__init__(company, **options)
        self.slug = slug or company.lower().replace(" ", "")

    def list(self) -> list[Posting]:
        data = _json(f"https://boards-api.greenhouse.io/v1/boards/{self.slug}/jobs?content=true")
        out = []
        for job in data.get("jobs", []):
            out.append(
                Posting(
                    company=self.company,
                    title=(job.get("title") or "").strip(),
                    location=(job.get("location") or {}).get("name", ""),
                    url=job.get("absolute_url", ""),
                    source=self.name,
                    description=_strip(job.get("content", "")),
                    department=", ".join(d.get("name", "") for d in job.get("departments", []) if d.get("name")),
                    posted_at=(job.get("updated_at") or "")[:10] or None,
                )
            )
        return out


class Lever(Source):
    name = "lever"

    def __init__(self, company: str = "", slug: str = "", **options: Any) -> None:
        super().__init__(company, **options)
        self.slug = slug or company.lower().replace(" ", "")

    def list(self) -> list[Posting]:
        data = _json(f"https://api.lever.co/v0/postings/{self.slug}?mode=json")
        out = []
        for job in data if isinstance(data, list) else []:
            categories = job.get("categories") or {}
            out.append(
                Posting(
                    company=self.company,
                    title=(job.get("text") or "").strip(),
                    location=categories.get("location") or "",
                    url=job.get("hostedUrl", ""),
                    source=self.name,
                    description=_strip(job.get("descriptionPlain") or job.get("description") or ""),
                    department=categories.get("team") or "",
                    posted_at=_epoch(job.get("createdAt")),
                )
            )
        return out


class Workable(Source):
    name = "workable"

    def __init__(self, company: str = "", slug: str = "", **options: Any) -> None:
        super().__init__(company, **options)
        self.slug = slug or company.lower().replace(" ", "")

    def list(self) -> list[Posting]:
        data = _json(f"https://apply.workable.com/api/v1/widget/accounts/{self.slug}?details=true")
        out = []
        for job in data.get("jobs", []) if isinstance(data, dict) else []:
            shortcode = job.get("shortcode", "")
            out.append(
                Posting(
                    company=self.company,
                    title=(job.get("title") or "").strip(),
                    location=", ".join(
                        part for part in (job.get("city"), job.get("state"), job.get("country")) if part
                    ),
                    url=job.get("url") or f"https://apply.workable.com/{self.slug}/j/{shortcode}/",
                    source=self.name,
                    description=_strip(job.get("description") or job.get("requirements") or ""),
                    department=job.get("department") or "",
                    posted_at=(job.get("published_on") or "")[:10] or None,
                )
            )
        return out


class Ashby(Source):
    name = "ashby"

    def __init__(self, company: str = "", slug: str = "", **options: Any) -> None:
        super().__init__(company, **options)
        self.slug = slug or company.lower().replace(" ", "")

    def list(self) -> list[Posting]:
        data = _json(f"https://api.ashbyhq.com/posting-api/job-board/{self.slug}")
        out = []
        for job in data.get("jobs", []):
            out.append(
                Posting(
                    company=self.company,
                    title=(job.get("title") or "").strip(),
                    location=job.get("location") or "",
                    url=job.get("jobUrl", ""),
                    source=self.name,
                    description=_strip(job.get("descriptionPlain") or job.get("descriptionHtml") or ""),
                    department=job.get("department") or job.get("team") or "",
                    posted_at=(job.get("publishedAt") or "")[:10] or None,
                )
            )
        return out


class Workday(Source):
    """Workday's own careers front-end, which is a JSON API in disguise.

    ``tenant`` is the subdomain, ``wd`` the node (wd1, wd3, wd5...), ``site`` the
    path segment after the node. The list endpoint takes a search term, so a
    target title is looked up directly rather than paging the whole board.
    """

    name = "workday"

    def __init__(
        self,
        company: str = "",
        tenant: str = "",
        wd: str = "wd3",
        site: str = "",
        **options: Any,
    ) -> None:
        super().__init__(company, **options)
        self.tenant = tenant
        self.wd = wd
        self.site = site
        self.root = f"https://{tenant}.{wd}.myworkdayjobs.com"
        self.api = f"{self.root}/wday/cxs/{tenant}/{site}"

    def search(self, term: str, limit: int = 20) -> list[Posting]:
        data = _json(
            f"{self.api}/jobs",
            body={"appliedFacets": {}, "limit": limit, "offset": 0, "searchText": term},
            retries=2,
        )
        out = []
        for job in data.get("jobPostings", []):
            path = job.get("externalPath", "")
            out.append(
                Posting(
                    company=self.company,
                    title=(job.get("title") or "").strip(),
                    location=job.get("locationsText") or "",
                    url=f"{self.root}/en-US/{self.site}{path}" if path else "",
                    source=self.name,
                    department=(job.get("bulletFields") or [""])[0] if job.get("bulletFields") else "",
                    posted_at=_relative_date(job.get("postedOn")),
                )
            )
        return out

    def list(self) -> list[Posting]:
        terms = self.options.get("terms") or [""]
        fast = bool(self.options.get("fast"))
        seen: dict[str, Posting] = {}
        failures = 0
        for term in terms:
            try:
                postings = self.search(str(term))
            except SourceError as error:
                # One 429 means the tenant is throttling; hammering the remaining
                # terms turns a pause into a lockout.
                failures += 1
                if fast or failures >= 2:
                    raise
                time.sleep(45)
                try:
                    postings = self.search(str(term))
                    failures = 0
                except SourceError:
                    raise error
            for posting in postings:
                if posting.url and posting.url not in seen:
                    seen[posting.url] = posting
            time.sleep(1.2)  # Workday throttles per tenant and is quick to 429
        return list(seen.values())

    def detail(self, posting: Posting) -> str:
        if not posting.url:
            return ""
        path = posting.url.split(self.site, 1)[-1]
        time.sleep(0.7)  # one description per kept job; pace them
        data = _json(f"{self.api}{path}", retries=2)
        info = data.get("jobPostingInfo") or {}
        return _strip(info.get("jobDescription") or "")


class Oracle(Source):
    """Oracle Recruiting Cloud, whose careers site is a REST API behind a JS shell.

    ``host`` is the tenant (``jpmc.fa.oraclecloud.com``) and ``site`` the site
    number from the candidate-experience URL (``CX_1001``). The finder endpoint
    takes a keyword, so a target title is looked up directly: JPMorgan alone has
    ~3,000 open requisitions and paging them all would be neither quick nor
    polite. The description is a second call, made only for postings that
    survived screening.

    The API's own URL is not stable across tenants, but its shape is: every
    Oracle HCM site serves ``/hcmRestApi/resources/latest`` and every job page
    lives at ``/hcmUI/CandidateExperience/<locale>/sites/<site>/job/<id>``.
    """

    name = "oracle"

    def __init__(
        self,
        company: str = "",
        host: str = "",
        site: str = "CX_1",
        **options: Any,
    ) -> None:
        super().__init__(company, **options)
        stem = company.lower().replace(" ", "").replace("&", "")
        self.host = host or f"{stem}.fa.oraclecloud.com"
        self.site = site or "CX_1"
        self.api = f"https://{self.host}/hcmRestApi/resources/latest"
        self.portal = f"https://{self.host}/hcmUI/CandidateExperience/en/sites/{self.site}"

    def search(self, term: str = "", limit: int = 100, offset: int = 0) -> list[Posting]:
        finder = f"findReqs;siteNumber={self.site}"
        if term:
            finder += f",keyword={term}"
        finder += f",limit={limit},offset={offset},sortBy=POSTING_DATES_DESC"
        data = _json(
            f"{self.api}/recruitingCEJobRequisitions"
            "?onlyData=true&expand=requisitionList.secondaryLocations"
            f"&finder={urllib.parse.quote(finder, safe=';=,')}"
        )
        items = data.get("items") or [{}]
        out = []
        for job in items[0].get("requisitionList", []):
            job_id = str(job.get("Id") or "")
            out.append(
                Posting(
                    company=self.company,
                    title=(job.get("Title") or "").strip(),
                    location=job.get("PrimaryLocation") or "",
                    url=f"{self.portal}/job/{job_id}" if job_id else "",
                    source=self.name,
                    description=_strip(job.get("ShortDescriptionStr") or ""),
                    department=job.get("Department") or job.get("JobFamily") or job.get("JobFunction") or "",
                    posted_at=(job.get("PostedDate") or "")[:10] or None,
                )
            )
        return out

    def list(self) -> list[Posting]:
        terms = self.options.get("terms") or [""]
        seen: dict[str, Posting] = {}
        for term in terms:
            for posting in self.search(str(term)):
                if posting.url and posting.url not in seen:
                    seen[posting.url] = posting
            time.sleep(PAUSE)
        return list(seen.values())

    def detail(self, posting: Posting) -> str:
        job_id = posting.url.rstrip("/").rsplit("/", 1)[-1] if posting.url else ""
        if not job_id:
            return posting.description
        finder = f'ById;Id="{job_id}",siteNumber={self.site}'
        data = _json(
            f"{self.api}/recruitingCEJobRequisitionDetails"
            f"?expand=all&onlyData=true&finder={urllib.parse.quote(finder, safe=';=,')}"
        )
        items = data.get("items") or [{}]
        detail = items[0]
        return _strip(
            detail.get("ExternalDescriptionStr")
            or detail.get("ShortDescriptionStr")
            or posting.description
        )


class Seek(Source):
    """SEEK's own search endpoint, which its front end calls.

    The per-job page is behind a bot wall, so this returns the teaser plus the
    advertised salary and work type. That is enough to triage a posting; the
    dashboard links to the real page for the full text and the apply button.
    """

    name = "seek"
    API = "https://www.seek.com.au/api/jobsearch/v5/search"

    def __init__(
        self,
        company: str = "",
        keywords: list[str] | None = None,
        where: str = "All Australia",
        pages: int = 1,
        **options: Any,
    ) -> None:
        super().__init__(company, **options)
        self.keywords = keywords or ["investment analyst"]
        self.where = where
        self.pages = pages

    def list(self) -> list[Posting]:
        out: list[Posting] = []
        seen: set[str] = set()
        for keyword in self.keywords:
            for page in range(1, self.pages + 1):
                query = urllib.parse.urlencode(
                    {
                        "siteKey": "AU-Main",
                        "sourcesystem": "houston",
                        "where": self.where,
                        "keywords": keyword,
                        "page": page,
                        "pagesize": 20,
                    }
                )
                data = _json(f"{self.API}?{query}")
                rows = data.get("data") or []
                if not rows:
                    break
                for job in rows:
                    job_id = str(job.get("id", ""))
                    if not job_id or job_id in seen:
                        continue
                    seen.add(job_id)
                    locations = ", ".join(
                        loc.get("label", "") for loc in (job.get("locations") or []) if loc.get("label")
                    )
                    salary = job.get("salaryLabel") or ""
                    work = (job.get("workArrangements") or {}).get("displayText") or ""
                    teaser = job.get("teaser") or ""
                    description = " ".join(part for part in (teaser, salary, work) if part).strip()
                    out.append(
                        Posting(
                            company=str((job.get("advertiser") or {}).get("description") or ""),
                            title=(job.get("title") or "").strip(),
                            location=locations,
                            url=f"https://www.seek.com.au/job/{job_id}",
                            source=self.name,
                            description=description,
                            department=_seek_classification(job),
                            posted_at=(job.get("listingDate") or "")[:10] or None,
                            country="au",
                        )
                    )
                time.sleep(PAUSE)
        return out


class SmartRecruiters(Source):
    """SmartRecruiters' public postings API."""

    name = "smartrecruiters"
    API = "https://api.smartrecruiters.com/v1/companies"

    def __init__(self, company: str = "", slug: str = "", **options: Any) -> None:
        super().__init__(company, **options)
        self.slug = slug

    def list(self) -> list[Posting]:
        out: list[Posting] = []
        offset = 0
        while offset < 200:
            data = _json(f"{self.API}/{self.slug}/postings?limit=100&offset={offset}")
            rows = data.get("content") or []
            if not rows:
                break
            for job in rows:
                location = job.get("location") or {}
                place = ", ".join(
                    part for part in (location.get("city"), location.get("country")) if part
                )
                out.append(
                    Posting(
                        company=self.company,
                        title=(job.get("name") or "").strip(),
                        location=place,
                        url=f"https://jobs.smartrecruiters.com/{self.slug}/{job.get('id')}",
                        source=self.name,
                        department=(job.get("department") or {}).get("label", ""),
                        posted_at=(job.get("releasedDate") or "")[:10] or None,
                    )
                )
            offset += 100
            total = data.get("totalFound") or 0
            if offset >= total:
                break
            time.sleep(PAUSE)
        return out

    def detail(self, posting: Posting) -> str:
        try:
            posting_id = posting.url.rsplit("/", 1)[-1]
            data = _json(f"{self.API}/{self.slug}/postings/{posting_id}")
            sections = ((data.get("jobAd") or {}).get("sections") or {})
            parts = [
                (sections.get("companyDescription") or {}).get("text", ""),
                (sections.get("jobDescription") or {}).get("text", ""),
                (sections.get("qualifications") or {}).get("text", ""),
            ]
            return _strip(" ".join(part for part in parts if part))
        except SourceError:
            return posting.description


class Adzuna(Source):
    """Adzuna aggregates employer feeds and its own crawls, UK and Australia.

    Needs a free app id and key (developer.adzuna.com). Without them the source
    reports itself as unavailable rather than silently returning nothing.
    """

    name = "adzuna"
    API = "https://api.adzuna.com/v1/api/jobs"

    def __init__(
        self,
        company: str = "",
        country: str = "gb",
        app_id: str = "",
        app_key: str = "",
        keywords: list[str] | None = None,
        where: str = "",
        pages: int = 1,
        results_per_page: int = 50,
        max_days_old: int = 21,
        **options: Any,
    ) -> None:
        super().__init__(company, **options)
        self.country = country
        self.app_id = app_id
        self.app_key = app_key
        self.keywords = keywords or ["investment analyst"]
        self.where = where
        self.pages = pages
        self.results_per_page = results_per_page
        self.max_days_old = max_days_old

    def available(self) -> bool:
        return bool(self.app_id and self.app_key)

    def list(self) -> list[Posting]:
        if not self.available():
            raise SourceError("adzuna needs app_id and app_key (free, developer.adzuna.com)")
        out: list[Posting] = []
        seen: set[str] = set()
        for keyword in self.keywords:
            for page in range(1, self.pages + 1):
                query = urllib.parse.urlencode(
                    {
                        "app_id": self.app_id,
                        "app_key": self.app_key,
                        "results_per_page": self.results_per_page,
                        "what": keyword,
                        "where": self.where,
                        "max_days_old": self.max_days_old,
                        "sort_by": "date",
                        "content-type": "application/json",
                    }
                )
                data = _json(f"{self.API}/{self.country}/search/{page}?{query}")
                rows = data.get("results") or []
                if not rows:
                    break
                for job in rows:
                    url = job.get("redirect_url") or ""
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    out.append(
                        Posting(
                            company=(job.get("company") or {}).get("display_name", ""),
                            title=(job.get("title") or "").strip(),
                            location=(job.get("location") or {}).get("display_name", ""),
                            url=url,
                            source=self.name,
                            description=_strip(job.get("description") or ""),
                            posted_at=(job.get("created") or "")[:10] or None,
                            country=self.country if self.country == "au" else "",
                        )
                    )
                time.sleep(PAUSE)
        return out


class Reed(Source):
    """Reed's job search API. UK only. Free key, no card."""

    name = "reed"
    API = "https://www.reed.co.uk/api/1.0/search"

    def __init__(
        self,
        company: str = "",
        api_key: str = "",
        keywords: list[str] | None = None,
        where: str = "",
        pages: int = 1,
        results_to_take: int = 100,
        **options: Any,
    ) -> None:
        super().__init__(company, **options)
        self.api_key = api_key
        self.keywords = keywords or ["investment analyst"]
        self.where = where
        self.pages = pages
        self.results_to_take = results_to_take

    def available(self) -> bool:
        return bool(self.api_key)

    def list(self) -> list[Posting]:
        if not self.available():
            raise SourceError("reed needs api_key (free, reed.co.uk/developers)")
        out: list[Posting] = []
        seen: set[str] = set()
        import base64

        token = base64.b64encode(f"{self.api_key}:".encode()).decode()
        for keyword in self.keywords:
            for page in range(1, self.pages + 1):
                query = urllib.parse.urlencode(
                    {
                        "keywords": keyword,
                        "locationName": self.where,
                        "resultsToTake": self.results_to_take,
                        "resultsToSkip": (page - 1) * self.results_to_take,
                    }
                )
                request = urllib.request.Request(
                    f"{self.API}?{query}",
                    headers={"authorization": f"Basic {token}", "user-agent": USER_AGENT},
                )
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    data = json.loads(response.read().decode("utf-8", errors="replace"))
                rows = data.get("results") or []
                if not rows:
                    break
                for job in rows:
                    url = job.get("jobUrl") or ""
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    out.append(
                        Posting(
                            company=job.get("employerName") or "",
                            title=(job.get("jobTitle") or "").strip(),
                            location=job.get("locationName") or "",
                            url=url,
                            source=self.name,
                            description=_strip(job.get("jobDescription") or ""),
                            posted_at=(job.get("date") or "")[:10] or None,
                        )
                    )
                time.sleep(PAUSE)
        return out


class IndeedBrowser(Source):
    """Indeed through a real browser.

    Indeed answers 403 to plain HTTP from here and its publisher API is closed to
    new users, but a normal Chromium window loads the results page and the cards
    are rendered. This drives that window with Playwright.

    Deliberate limits, because none of this is welcome by default: no login, one
    page every second or so, a page cap, and no attempt to defeat a challenge. If
    Indeed shows a CAPTCHA this returns nothing and says so rather than trying to
    solve it.

    It is off unless you switch it on in firms.toml. Indeed's terms prohibit
    automated collection; whether to run this is your call, not a default.
    """

    name = "indeed"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    CARD = '[data-testid="slider_item"]'
    EXTRACT = """
    (el) => {
      const anchor = el.querySelector('h3.jobTitle a[data-jk], a[data-jk]');
      if (!anchor) return null;
      const jk = anchor.getAttribute('data-jk');
      if (!jk) return null;
      const heading = el.querySelector('h3.jobTitle');
      const title = heading ? heading.innerText.trim()
                            : (anchor.getAttribute('aria-label') || '').replace('full details of ', '');
      const text = (sel) => { const n = el.querySelector(sel); return n ? n.innerText.trim() : ''; };
      return {
        title: title,
        company: text('[data-testid="company-name"]'),
        location: text('[data-testid="text-location"]'),
        jk: jk,
      };
    }
    """

    def __init__(
        self,
        company: str = "",
        country: str = "uk",
        keywords: list[str] | None = None,
        where: str = "London",
        pages: int = 1,
        headless: bool = True,
        fetch_details: bool = True,
        max_jobs: int = 40,
        session: str = "",
        **options: Any,
    ) -> None:
        super().__init__(company, **options)
        self.country = country
        self.keywords = keywords or ["investment analyst"]
        self.where = where
        self.pages = pages
        self.headless = headless
        self.fetch_details = fetch_details
        self.max_jobs = max_jobs
        self.session = session or str(Path.home() / ".jobhunter" / "sessions" / "indeed.json")

    def list(self) -> list[Posting]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:  # pragma: no cover - environment specific
            raise SourceError("indeed needs playwright: pip install playwright && playwright install chromium") from error

        host = "https://au.indeed.com" if self.country == "au" else "https://uk.indeed.com"
        out: list[Posting] = []
        seen: set[str] = set()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent=self.USER_AGENT,
                locale="en-GB" if self.country == "uk" else "en-AU",
                viewport={"width": 1280, "height": 900},
                storage_state=self.session if self.session and Path(self.session).is_file() else None,
            )
            # Chromium announces itself as automated; nothing here needs that flag.
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            page = context.new_page()
            try:
                for keyword in self.keywords:
                    for page_number in range(self.pages):
                        query = urllib.parse.urlencode(
                            {"q": keyword, "l": self.where, "start": page_number * 10}
                        )
                        page.goto(f"{host}/jobs?{query}", wait_until="domcontentloaded", timeout=45_000)
                        page.wait_for_timeout(1500)
                        body = page.inner_text("body")[:4000]
                        if "additional verification" in body.lower() or "captcha" in body.lower():
                            raise SourceError("indeed served a verification challenge; stopping rather than solving it")
                        for card in page.query_selector_all(self.CARD):
                            data = card.evaluate(self.EXTRACT)
                            if not data or not data.get("jk"):
                                continue
                            if data["jk"] in seen:
                                continue
                            seen.add(data["jk"])
                            out.append(
                                Posting(
                                    company=data["company"],
                                    title=data["title"],
                                    location=data["location"],
                                    url=f"{host}/viewjob?jk={data['jk']}",
                                    source=self.name,
                                    country=self.country,
                                )
                            )
                            if len(out) >= self.max_jobs:
                                break
                        page.wait_for_timeout(1000)
                        if len(out) >= self.max_jobs:
                            break
                    if len(out) >= self.max_jobs:
                        break
            finally:
                context.close()
                browser.close()
        return out

    def detail(self, posting: Posting) -> str:
        if not self.fetch_details:
            return posting.description
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover
            return posting.description

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(user_agent=self.USER_AGENT, locale="en-GB")
            page = context.new_page()
            try:
                page.goto(posting.url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_selector("#jobDescriptionText", timeout=15_000)
                text = page.inner_text("#jobDescriptionText")
            except Exception:
                text = ""
            finally:
                context.close()
                browser.close()
        return _strip(text)


class JobSpy(Source):
    """JobSpy (github.com/speedyapply/JobSpy), for Indeed and its siblings.

    JobSpy is MIT, maintained, and does the two things this repository did not:
    it reaches Indeed at all, and it resolves ``job_url_direct`` — the employer's
    own posting behind the aggregator — for most rows. Storing that direct URL
    means a role found on Indeed and later seen on the employer's Workday board
    is recognised as the same job by the fingerprint.

    It is used as a library, not vendored, so its fixes arrive with ``pip -U``.
    """

    name = "jobspy"
    COUNTRIES = {"uk": "UK", "au": "Australia", "us": "USA", "ca": "Canada"}

    def __init__(
        self,
        company: str = "",
        sites: list[str] | None = None,
        country: str = "uk",
        keywords: list[str] | None = None,
        where: str = "London",
        results_wanted: int = 40,
        hours_old: int | None = None,
        **options: Any,
    ) -> None:
        super().__init__(company, **options)
        self.sites = sites or ["indeed"]
        self.country = country
        self.keywords = keywords or ["investment analyst"]
        self.where = where
        self.results_wanted = results_wanted
        self.hours_old = hours_old

    def list(self) -> list[Posting]:
        try:
            from jobspy import scrape_jobs
        except ImportError as error:  # pragma: no cover
            raise SourceError("jobspy is not installed: pip install python-jobspy") from error

        out: list[Posting] = []
        seen: set[str] = set()
        for keyword in self.keywords:
            try:
                frame = scrape_jobs(
                    site_name=self.sites,
                    search_term=keyword,
                    location=self.where,
                    country_indeed=self.COUNTRIES.get(self.country, "UK"),
                    results_wanted=self.results_wanted,
                    hours_old=self.hours_old,
                    verbose=0,
                )
            except Exception as error:  # noqa: BLE001 - a blocked search is not a crash
                raise SourceError(f"jobspy {keyword!r}: {type(error).__name__}: {error}") from error

            for row in frame.itertuples(index=False):
                direct = _clean_cell(getattr(row, "job_url_direct", ""))
                aggregator = _clean_cell(getattr(row, "job_url", ""))
                url = direct if direct.startswith("http") else aggregator
                if not url or url in seen:
                    continue
                seen.add(url)
                title = _clean_cell(getattr(row, "title", ""))
                if not title:
                    continue
                salary = _salary(getattr(row, "min_amount", None), getattr(row, "max_amount", None),
                                 _clean_cell(getattr(row, "currency", "")))
                out.append(
                    Posting(
                        company=_clean_cell(getattr(row, "company", "")),
                        title=title,
                        location=_clean_cell(getattr(row, "location", "")),
                        url=url,
                        source=f"jobspy:{_clean_cell(getattr(row, 'site', 'indeed'))}",
                        description=_clean_cell(getattr(row, "description", "")),
                        posted_at=_clean_cell(getattr(row, "date_posted", ""))[:10] or None,
                        country=self.country,
                        salary=salary,
                    )
                )
        return out


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return "" if text.lower() in ("nan", "none", "nat") else text.strip()


def _salary(low: Any, high: Any, currency: str) -> str:
    def number(value: Any) -> int | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return None if result != result else int(result)  # NaN check

    low_value, high_value = number(low), number(high)
    if low_value is None and high_value is None:
        return ""
    if low_value is not None and high_value is not None and low_value != high_value:
        return f"{currency} {low_value:,}–{high_value:,}".strip()
    single = low_value if low_value is not None else high_value
    return f"{currency} {single:,}".strip()


def _seek_classification(job: dict) -> str:
    out = []
    for item in job.get("classifications") or []:
        sub = (item.get("subclassification") or {}).get("description")
        cls = (item.get("classification") or {}).get("description")
        if sub or cls:
            out.append(sub or cls)
    return ", ".join(out)


def _strip(html: str) -> str:
    """Text out of HTML, with block tags kept as line breaks.

    Entities are decoded *first*: Greenhouse returns escaped markup
    (``&lt;div&gt;``), so stripping tags before decoding leaves the tags in the
    text, and the screening regexes then read ``<div class="content-intro">`` as
    a requirement. Crude on purpose otherwise — a parser library would add a
    dependency for no gain in accuracy.
    """
    if not html:
        return ""
    import html as html_module
    import re

    text = html_module.unescape(html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|li|div|h[1-6])>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _epoch(value: Any) -> str | None:
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(int(value) / 1000))
    except (TypeError, ValueError, OSError):
        return None


def _relative_date(value: Any) -> str | None:
    """Workday says "Posted 3 Days Ago"; keep it as-is rather than guess a date."""
    return str(value) if value else None


KINDS = {
    "greenhouse": Greenhouse,
    "lever": Lever,
    "workable": Workable,
    "ashby": Ashby,
    "workday": Workday,
    "oracle": Oracle,
    "seek": Seek,
    "smartrecruiters": SmartRecruiters,
    "adzuna": Adzuna,
    "reed": Reed,
    "indeed": IndeedBrowser,
    "jobspy": JobSpy,
}


def build(entry: dict, title_terms: list[str] | None = None) -> Source:
    """Create a source from one ``firms.toml`` entry."""
    kind = entry.get("ats", "")
    if kind not in KINDS:
        raise SourceError(f"unknown ats {kind!r} for {entry.get('name')!r}")
    cls = KINDS[kind]
    options = {k: v for k, v in entry.items() if k not in {"name", "ats", "notes"}}
    if kind in ("workday", "oracle"):
        options["terms"] = title_terms or [""]
    if kind == "adzuna":
        # firms.toml says "uk" like everything else; Adzuna names its markets gb/au.
        options["country"] = {"uk": "gb"}.get(str(entry.get("country", "")), entry.get("country") or "gb")
    return cls(company=str(entry.get("name", "")), **options)
