"""Read a posting and say whether it is worth applying to.

Two different things are deliberately kept apart:

* The *form* (which fields an ATS shows) is a later concern. The vendored
  plugins in ``vendor/aihawk/plugins`` describe it.
* The *screening* is server-side and cannot be read from a browser. What can be
  read is the knockout filter a recruiter puts in the posting: sponsorship,
  clearance, years, salary floor, work mode. Those reject applications silently,
  so they are extracted first and cheaply, before any browser opens.

Everything here is deterministic. No model call, no network. The output is a
filter, not a verdict: ``coverage`` is token overlap over requirement sentences
and is a heuristic, which the report says out loud.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Decision = Literal["exclude", "ask", "apply"]

# Words that carry no signal when comparing a requirement to a CV.
STOPWORDS = frozenset(
    """
    a an and are as at be been being but by can could do does for from has have
    having in into is it its may might must need needs of on or our shall should
    so than that the their them then there these they this those to us was we
    were what when which while who will with within would you your yours able
    about above across after all also any applicant applicants application
    apply candidate candidates company demonstrate demonstrated demonstrates
    essential experience experienced ideal ideally including job knowledge
    minimum must-have opportunity please plus preferred proficiency proficient
    proven provide provides qualifications required requirement requirements
    responsibilities role skills strong successful suitable team teams etc
    ability work working years year plus least professional such including
    """.split()
)

# A requirement sentence usually says one of these.
REQUIREMENT_CUES = re.compile(
    r"\b(must|required|essential|minimum|at least|you (?:will )?(?:have|need)|"
    r"proficien\w*|knowledge of|experience (?:in|with|of)|familiar\w*|"
    r"strong|excellent|ability to|demonstrable|track record)\b",
    re.IGNORECASE,
)

SPONSORSHIP_NONE = re.compile(
    r"\b(?:no|not)\s+(?:visa\s+)?sponsorship\b"
    r"|\bsponsorship\s+(?:is\s+|will\s+)?not\s+(?:available|offered|provided|possible)\b"
    r"|\b(?:we|the company|this (?:role|position|employer))\s+"
    r"(?:do not|don't|does not|doesn't|cannot|can't|will not|won't|are unable to|is unable to)\s+"
    r"(?:offer|provide|support|consider)?\s*(?:visa\s+)?sponsorship\b"
    r"|\bunable to (?:offer|provide) (?:visa )?sponsorship\b"
    r"|\bwithout (?:the need for )?(?:visa )?sponsorship\b",
    re.IGNORECASE,
)

SPONSORSHIP_YES = re.compile(
    r"\b(?:visa\s+)?sponsorship\s+(?:is\s+)?(?:available|offered|provided)\b"
    r"|\bwe (?:do )?sponsor\b|\bwill sponsor\b",
    re.IGNORECASE,
)

RIGHT_TO_WORK = re.compile(
    r"\bright to work\b|\bwork(?:ing)? authori[sz]ation\b|\bwork permit\b",
    re.IGNORECASE,
)

MONEY = re.compile(r"[£$€]\s?\d[\d,]*(?:\.\d+)?\s?(?:k\b)?", re.IGNORECASE)
MONEY_RANGE = re.compile(
    r"[£$€]\s?(\d[\d,]*(?:\.\d+)?)\s?(k)?\s*(?:-|–|—|to)\s*[£$€]?\s?"
    r"(\d[\d,]*(?:\.\d+)?)\s?(k)?",
    re.IGNORECASE,
)
SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}

YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)

CLEARANCE_CODES = re.compile(r"\b(SC|DV|CTC|BPSS|NPPV)\b", re.IGNORECASE)
CLEARANCE_PHRASES = re.compile(
    r"\bsecurity clearance\b|\bsecurity check(?:ed)?\b|\b(?:enhanced\s+)?DBS\s+(?:check|certificate)\b",
    re.IGNORECASE,
)

ON_SITE = re.compile(
    r"\bfully on-?site\b|\ball (?:five|5) days\b|\b(?:five|5) days (?:a|per) week in (?:the )?office\b"
    r"|\bno remote\b|\boffice[- ]based only\b|\bon-?site only\b",
    re.IGNORECASE,
)
REMOTE = re.compile(r"\bfully remote\b|\bremote[- ]first\b|\bwork from home\b|\bremote\b", re.IGNORECASE)


@dataclass(frozen=True)
class Candidate:
    """Only the facts that change a decision. Everything else lives in the components bank."""

    needs_sponsorship: bool = False
    salary_floor: int | None = None
    currency: str = "GBP"
    remote_only: bool = False
    years_experience: float | None = None
    clearances: tuple[str, ...] = ()
    right_to_work: bool | None = None


@dataclass(frozen=True)
class Finding:
    name: str
    kind: Literal["blocker", "check", "flag"]
    evidence: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.name} — {self.evidence}"


@dataclass(frozen=True)
class ScreenReport:
    decision: Decision
    blockers: tuple[Finding, ...] = ()
    checks: tuple[Finding, ...] = ()
    flags: tuple[Finding, ...] = ()
    requirements: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    coverage: float | None = None
    salary: tuple[str, int, int] | None = None
    note: str = "coverage is token overlap, a heuristic, not a judgement"

    def summary(self) -> str:
        lines = [f"decision: {self.decision}"]
        for group, label in ((self.blockers, "blocker"), (self.checks, "check"), (self.flags, "flag")):
            for finding in group:
                lines.append(f"  {label}: {finding.name} — {finding.evidence}")
        if self.salary:
            currency, low, high = self.salary
            lines.append(f"  salary: {currency} {low:,}–{high:,}")
        if self.coverage is not None:
            lines.append(f"  coverage: {self.coverage:.0%} of {len(self.requirements)} requirements")
        for item in self.missing:
            lines.append(f"  missing: {_clip(item, 88)}")
        return "\n".join(lines)


def screen(posting: str, cv: str, candidate: Candidate | None = None) -> ScreenReport:
    """The report for one posting against one CV."""
    who = candidate or Candidate()
    blocks: list[Finding] = []
    checks: list[Finding] = []
    flags: list[Finding] = []

    if SPONSORSHIP_NONE.search(posting):
        evidence = _clip(_match_text(SPONSORSHIP_NONE, posting), 100)
        if who.needs_sponsorship:
            blocks.append(Finding("sponsorship-unavailable", "blocker", evidence))
        else:
            flags.append(Finding("no-sponsorship", "flag", evidence))
    elif SPONSORSHIP_YES.search(posting):
        flags.append(Finding("sponsorship-available", "flag", _clip(_match_text(SPONSORSHIP_YES, posting), 100)))

    if RIGHT_TO_WORK.search(posting) and not SPONSORSHIP_NONE.search(posting):
        evidence = _clip(_match_text(RIGHT_TO_WORK, posting), 100)
        if who.right_to_work is True:
            flags.append(Finding("right-to-work-held", "flag", evidence))
        elif who.right_to_work is False:
            blocks.append(Finding("right-to-work", "blocker", evidence + " — profile says it is not held"))
        else:
            checks.append(Finding("right-to-work", "check", evidence + " — set candidate.right_to_work to settle this"))

    clearance = _clearances(posting)
    if clearance:
        needed = {c.upper() for c in clearance}
        held = {c.upper() for c in who.clearances}
        missing = tuple(sorted(needed - held))
        if missing:
            blocks.append(Finding("clearance", "blocker", "requires " + ", ".join(missing) + " which the profile lacks"))
        else:
            flags.append(Finding("clearance-held", "flag", "requires " + ", ".join(sorted(needed))))

    salary = _salary(posting)
    if salary:
        currency, low, high = salary
        if (
            who.salary_floor is not None
            and currency == who.currency
            and high < who.salary_floor
        ):
            blocks.append(
                Finding(
                    "salary-below-floor",
                    "blocker",
                    f"{currency} {low:,}–{high:,} is under the {who.currency} {who.salary_floor:,} floor",
                )
            )
        else:
            flags.append(Finding("salary", "flag", f"{currency} {low:,}–{high:,}"))

    years = _years(posting)
    if years and who.years_experience is not None:
        need = max(years)
        if who.years_experience < need:
            checks.append(
                Finding(
                    "years",
                    "check",
                    f"asks {need}+ years, profile has {who.years_experience:g} — apply only if the rest is strong",
                )
            )
        else:
            flags.append(Finding("years", "flag", f"asks {need}+ years"))

    if ON_SITE.search(posting):
        evidence = _clip(_match_text(ON_SITE, posting), 100)
        if who.remote_only:
            blocks.append(Finding("not-remote", "blocker", evidence))
        else:
            flags.append(Finding("on-site", "flag", evidence))
    elif REMOTE.search(posting):
        flags.append(Finding("remote", "flag", "remote is mentioned"))

    requirements = _requirements(posting)
    missing, coverage = _coverage(requirements, cv)

    if blocks:
        decision: Decision = "exclude"
    elif checks:
        decision = "ask"
    else:
        decision = "apply"

    return ScreenReport(
        decision=decision,
        blockers=tuple(blocks),
        checks=tuple(checks),
        flags=tuple(flags),
        requirements=requirements,
        missing=missing,
        coverage=coverage,
        salary=salary,
    )


# --- pieces --------------------------------------------------------------


BOILERPLATE = re.compile(
    r"\b(hybrid|working model|benefit|pension|holiday|annual leave|discount|gym|"
    r"medical|life assurance|share scheme|referral|salary|bonus|equal opportunit\w*|"
    r"diversity|inclusion|we believe|our culture|social event|flexible working|"
    r"application process|interview process|reasonable adjustment\w*|visa|sponsor\w*)\b",
    re.I,
)


def _requirements(posting: str) -> tuple[str, ...]:
    """Sentences that read like a requirement, deduplicated, capped.

    Two filters, and the second matters more than it looks. A cue word like
    "strong" appears in "we believe in building strong connections", which is
    culture copy, not a requirement; scoring the CV against it produces noise and
    drags coverage down for no reason. Benefits and process sentences go the same
    way.
    """
    seen: set[str] = set()
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n\s*[-•*]\s*", posting):
        clean = " ".join(sentence.split())
        if len(clean) < 25 or not REQUIREMENT_CUES.search(clean):
            continue
        if BOILERPLATE.search(clean):
            continue
        key = clean.lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(clean.rstrip("."))
        if len(out) >= 25:
            break
    return tuple(out)


def _tokens(text: str) -> set[str]:
    """Content tokens, singularised, so "stakeholders" matches "stakeholder"."""
    out: set[str] = set()
    for word in re.findall(r"[A-Za-z][A-Za-z+.#-]{2,}", text):
        lower = word.lower()
        if lower in STOPWORDS:
            continue
        out.add(lower[:-1] if len(lower) > 4 and lower.endswith("s") and not lower.endswith("ss") else lower)
    return out


def _coverage(requirements: tuple[str, ...], cv: str) -> tuple[tuple[str, ...], float | None]:
    """How much of each requirement the CV evidences, by token overlap.

    Graded rather than binary. The earlier rule — 50% of *every* token in the
    sentence — made long requirements impossible: a 30-word sentence needed
    fifteen matches, so a genuinely relevant posting scored zero. Here the
    denominator is capped, so a long sentence needs a handful of the right words
    rather than half of all of them, and each requirement contributes its own
    fraction to the total.

    Still a heuristic, and still reported as one.
    """
    if not requirements or not cv.strip():
        return (), None
    cv_tokens = _tokens(cv)
    if not cv_tokens:
        return (), None

    missing: list[str] = []
    total = 0.0
    counted = 0
    for requirement in requirements:
        needed = _tokens(requirement)
        if not needed:
            continue
        matched = len(needed & cv_tokens)
        denominator = max(3, min(len(needed), 10))
        score = min(1.0, matched / denominator)
        total += score
        counted += 1
        if score < 0.5:
            missing.append(requirement)
    return tuple(missing), (total / counted if counted else None)


def _salary(posting: str) -> tuple[str, int, int] | None:
    """The widest range stated with a currency symbol, in whole units."""
    values: list[int] = []
    currency = ""
    for match in MONEY_RANGE.finditer(posting):
        low, low_k, high, high_k = match.groups()
        currency = currency or _currency(match.group(0))
        values.extend([_units(low, low_k), _units(high, high_k or low_k)])
    if not values:
        for match in MONEY.finditer(posting):
            currency = currency or _currency(match.group(0))
            values.append(_units(match.group(0).split()[-1], "k" in match.group(0).lower()))
    digits = [v for v in values if v >= 1000]
    if not digits or not currency:
        return None
    return currency, min(digits), max(digits)


def _units(text: str, thousands: bool | str | None) -> int:
    number = re.sub(r"[^\d.]", "", text.replace(",", ""))
    if not number:
        return 0
    try:
        value = float(number)
    except ValueError:
        return 0
    if thousands or (value < 1000 and value <= 500):
        value *= 1000
    return int(value)


def _currency(text: str) -> str:
    for symbol, code in SYMBOLS.items():
        if symbol in text:
            return code
    return ""


def _clearances(posting: str) -> tuple[str, ...]:
    """Clearance names the posting asks for, normalised to their code.

    The code is what a person holds, so "SC clearance" and "SC" must compare
    equal; keeping the phrase would make every holder look like they lack it.
    """
    found: set[str] = set()
    for match in CLEARANCE_CODES.finditer(posting):
        window = posting[max(0, match.start() - 40) : match.end() + 40].lower()
        if "clearance" in window or "checked" in window or "check" in window:
            found.add(match.group(1).upper())
    if CLEARANCE_PHRASES.search(posting) and not found:
        phrase = " ".join(CLEARANCE_PHRASES.search(posting).group(0).split())  # type: ignore[union-attr]
        found.add(phrase.upper())
    return tuple(sorted(found))


def _years(posting: str) -> list[int]:
    return sorted({int(m.group(1)) for m in YEARS.finditer(posting) if 0 < int(m.group(1)) < 40})


def _match_text(pattern: re.Pattern[str], text: str) -> str:
    """A short quote around the match, snapped to word boundaries.

    Without the snap the quote starts mid-word and reads like corruption
    ("ttee. Requirements - You must...").
    """
    match = pattern.search(text)
    if not match:
        return ""
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + 40)
    window = text[start:end]
    if start > 0 and " " in window:
        window = window.split(" ", 1)[-1]
    if end < len(text) and " " in window:
        window = window.rsplit(" ", 1)[0]
    return " ".join(window.split())


def _clip(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"
