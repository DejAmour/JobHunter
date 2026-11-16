"""Read a CV the way an applicant-tracking system reads it, and report what it loses.

The point is not to score the CV. It is to find the places where a parser sees a
worse document than a human does, because those are invisible failures: the
application is submitted, the profile field is blank, and the filter runs on
nothing.

Everything here is local and deterministic. Text extraction only — no model, no
network. ``warnings`` are things a parser commonly mishandles; ``notes`` are
things a reader would notice.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# Headings a parser is trained to recognise. Anything else is body text to it.
CANONICAL_SECTIONS = {
    "summary": ("summary", "profile", "objective", "about"),
    "education": ("education", "academic", "qualifications"),
    "experience": ("experience", "employment", "work history", "professional experience"),
    "skills": ("skills", "technical skills", "competencies", "expertise"),
    "projects": ("projects", "portfolio", "selected work"),
    "certifications": ("certifications", "certificates", "professional qualifications", "licences", "licenses"),
    "awards": ("awards", "honours", "honors", "achievements"),
    "publications": ("publications", "papers", "research"),
    "languages": ("languages",),
    "interests": ("interests", "extracurricular", "hobbies", "activities"),
}

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
DATE_RANGE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*\d{2,4}|\d{1,2}/\d{2,4}|\d{4})"
    r"\s*(?:-|–|—|to)\s*"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*\d{2,4}|\d{1,2}/\d{2,4}|\d{4}|present|current|now)",
    re.IGNORECASE,
)
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1
)}

# Wingdings and Symbol glyphs, which PDF exports use for bullets. Parsers drop them.
# U+2022 (•) is deliberately *not* here: it is a normal character and parses fine.
PRIVATE_BULLETS = re.compile("[\uf0a7\uf0b7\uf0d8\uf0fc\uf06c\uf076]")
# Text that has been double-encoded somewhere between the document and the text
# layer. Seen in the wild as "â€¢" for a bullet, which every parser then keeps.
MOJIBAKE = re.compile("â€|Ã¢|ï»¿|Â[\u00a0-\u00bf]|Ã[\u0080-\u00bf]")
DEGREE_CLASS = re.compile(r"\b(first|1st|upper second|2:1|lower second|2:2|third|distinction|merit|pass)\b", re.IGNORECASE)
DEGREE_WORD = re.compile(r"\b(bachelor|bsc|ba\b|beng|meng|msc|mba|mphil|phd|dphil|foundation degree|hnd|btec)\b", re.IGNORECASE)


@dataclass
class CvAudit:
    path: str
    kind: str
    pages: int | None
    characters: int
    name: str = ""
    email: str = ""
    phone: str = ""
    sections: dict[str, int] = field(default_factory=dict)
    headings_found: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    present_terms: tuple[str, ...] = ()
    absent_terms: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    confidence: int = 0

    def report(self) -> str:
        lines = [
            f"{Path(self.path).name} — {self.kind}, {self.pages or '?'} page(s), {self.characters:,} characters",
            f"parse confidence: {self.confidence}/100",
            "",
            f"name:  {self.name or '(not found)'}",
            f"email: {self.email or '(not found)'}",
            f"phone: {self.phone or '(not found)'}",
            f"sections recognised: {', '.join(self.headings_found) or '(none)'}",
            f"date ranges: {len(self.dates)}",
        ]
        if self.present_terms:
            lines.append(f"target terms present: {', '.join(self.present_terms)}")
        if self.absent_terms:
            lines.append(f"target terms absent:  {', '.join(self.absent_terms)}")
        if self.warnings:
            lines += ["", "warnings (a parser will lose this):"]
            lines += [f"  ! {w}" for w in self.warnings]
        if self.notes:
            lines += ["", "notes:"]
            lines += [f"  - {n}" for n in self.notes]
        return "\n".join(lines)


def extract(path: Path) -> tuple[str, str, int | None]:
    """Text, kind, page count. Raises FileNotFoundError or ValueError."""
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf(path)
    if suffix == ".docx":
        return _docx(path)
    if suffix in {".txt", ".md", ".markdown"}:
        return path.read_text(encoding="utf-8-sig"), "text", None
    raise ValueError(f"unsupported: {suffix} (use pdf, docx, txt)")


def _pdf(path: Path) -> tuple[str, str, int | None]:
    try:
        from pypdf import PdfReader
    except ImportError as error:  # pragma: no cover
        raise ValueError("reading PDFs needs pypdf: python -m pip install pypdf") from error
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages), "pdf", len(reader.pages)


def _docx(path: Path) -> tuple[str, str, int | None]:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", "replace")
    text = xml.replace("</w:p>", "\n").replace("<w:tab/>", "\t").replace("<w:br/>", "\n")
    text = re.sub(r"<[^>]+>", "", text)
    pages = len(re.findall(r'w:br w:type="page"', xml)) + 1
    return text, "docx", pages


def audit(path: Path, terms: tuple[str, ...] = ()) -> CvAudit:
    text, kind, pages = extract(path)
    clean = "\n".join(line.strip() for line in text.splitlines())

    email = EMAIL.search(clean)
    phone = PHONE.search(clean)
    name = _name(clean)
    headings = _headings(clean)
    sections = _section_sizes(clean, headings)
    dates = _date_ranges(clean)

    present, absent = _terms(clean, terms)

    warnings: list[str] = []
    notes: list[str] = []

    if PRIVATE_BULLETS.search(text):
        warnings.append(
            "bullets use a private-use glyph (Wingdings/Symbol); many parsers drop the whole line"
        )
    if MOJIBAKE.search(text):
        warnings.append(
            "text is double-encoded in the text layer (e.g. 'â€¢' for a bullet); "
            "parsers will read the garbage, not the word — re-save with a Latin-1 safe bullet"
        )
    if kind == "docx":
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "replace")
        if "w:txbxContent" in xml:
            warnings.append("text is in text boxes; most parsers skip text boxes entirely")
    if kind == "pdf" and pages and len(clean) < 400 * pages:
        warnings.append(
            f"only {len(clean)} characters across {pages} page(s) — probably a scan or image text, not a text layer"
        )
    if not email:
        warnings.append("no email address found in the text layer")
    if not phone:
        warnings.append("no phone number found in the text layer")
    if "skills" not in sections:
        warnings.append(
            "no 'Skills' section: keyword matching and skills extraction both degrade without one"
        )
    if "education" not in sections:
        warnings.append("no 'Education' heading; degree extraction is unreliable without it")

    degrees = DEGREE_WORD.findall(clean)
    if not any(d.lower() in {"bachelor", "bsc", "ba", "beng", "meng"} for d in degrees) and DEGREE_CLASS.search(clean) is None:
        notes.append("no undergraduate degree or degree classification stated")
    if not DEGREE_CLASS.search(clean):
        notes.append(
            "no classification (2:1, Distinction, ...) — graduate schemes often ask for it as a mandatory field"
        )
    if len(dates) > 1 and _overlaps(dates):
        notes.append("two date ranges overlap, which some parsers read as a single role")
    if len(clean) > 9000:
        notes.append(f"{len(clean):,} characters is long for one page; consider trimming")

    confidence = 100.0
    confidence -= 18 * len(warnings)
    if not headings:
        confidence -= 15
    if kind == "pdf" and pages and len(clean) < 400 * pages:
        confidence -= 25
    confidence = max(0, min(100, int(confidence)))

    return CvAudit(
        path=str(path),
        kind=kind,
        pages=pages,
        characters=len(clean),
        name=name,
        email=email.group(0) if email else "",
        phone=phone.group(0).strip() if phone else "",
        sections=sections,
        headings_found=headings,
        dates=dates,
        present_terms=present,
        absent_terms=absent,
        warnings=tuple(warnings),
        notes=tuple(notes),
        confidence=confidence,
    )


def _name(text: str) -> str:
    """The first line that looks like a person, not a heading or a contact."""
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 45:
            continue
        if EMAIL.search(line) or PHONE.search(line) or "@" in line:
            continue
        words = line.split()
        if 2 <= len(words) <= 4 and all(w[:1].isupper() for w in words if w.isalpha()):
            if not any(w.isupper() and len(w) > 3 for w in words):
                return line
    return ""


def _headings(text: str) -> tuple[str, ...]:
    """A heading is short, not a sentence, and not a fragment of one.

    Without the length and punctuation guards a wrapped bullet is read as a
    heading: "research and stock shortlisting." starts with "research", which is
    enough to make a parser think a Publications section began.
    """
    found: list[str] = []
    for line in text.splitlines():
        key = line.strip().lower().strip(":").strip()
        if not key or len(key) > 32 or key.endswith((".", ",", ";", ")")):
            continue
        if len(key.split()) > 5:
            continue
        for name, words in CANONICAL_SECTIONS.items():
            if key in words or any(key.startswith(w + " ") for w in words):
                if name not in found:
                    found.append(name)
    return tuple(found)


def _section_sizes(text: str, headings: tuple[str, ...]) -> dict[str, int]:
    return {name: 1 for name in headings}


def _date_ranges(text: str) -> tuple[str, ...]:
    return tuple(f"{a} – {b}" for a, b in DATE_RANGE.findall(text))


def _month_year(value: str) -> tuple[int, int] | None:
    value = value.strip().lower()
    if value in {"present", "current", "now"}:
        return (9999, 12)
    match = re.match(r"([a-z]{3,9})\.?\s*(\d{2,4})", value)
    if match and match.group(1)[:3] in MONTHS:
        year = int(match.group(2))
        return (year + 2000 if year < 100 else year, MONTHS[match.group(1)[:3]])
    if re.fullmatch(r"\d{4}", value):
        return (int(value), 1)
    match = re.match(r"(\d{1,2})/(\d{2,4})", value)
    if match:
        year = int(match.group(2))
        return (year + 2000 if year < 100 else year, int(match.group(1)))
    return None


def _overlaps(dates: tuple[str, ...]) -> bool:
    spans = []
    for pair in dates:
        start_text, _, end_text = pair.partition(" – ")
        start, end = _month_year(start_text), _month_year(end_text)
        if start and end:
            spans.append((start, end))
    spans.sort()
    for (_, end), (next_start, _) in zip(spans, spans[1:]):
        if next_start < end:
            return True
    return False


def _terms(text: str, terms: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Hyphens and dashes are normalised, because "asset-allocation" is "asset allocation".

    Without this the report says a term is missing when the CV merely wrote it
    with a hyphen, which would send you chasing a gap that is not there.
    """
    if not terms:
        return (), ()

    def norm(value: str) -> str:
        return re.sub(r"[\s\u2010-\u2015-]+", " ", value.lower()).strip()

    haystack = " " + norm(text) + " "
    present = [t for t in terms if norm(t) in haystack]
    absent = [t for t in terms if norm(t) not in haystack]
    return tuple(present), tuple(absent)
