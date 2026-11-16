"""Render the CV from cv/content.toml into a DOCX and a plain-text version.

Three things are deliberate:

* Bullets are a literal middle dot (U+00B7), not Word's list style. Word's list
  style uses the Symbol font and exports as U+F0B7, which many parsers drop on
  the floor. U+2022 looks better but came out of Word's PDF export double-encoded
  as "a-euro-o" garbage, so the Latin-1 middle dot is what survives.
* Headings use the exact words a parser looks for ("SKILLS", "QUALIFICATIONS",
  "PROFESSIONAL EXPERIENCE"), because a parser matches headings, not layout.
* A variant file may set ``base`` to inherit the master and override only what
  differs, so three CVs do not drift into three sets of facts.

Usage:
    python scripts/build_cv.py              # master only
    python scripts/build_cv.py --all        # master plus every variant
    python scripts/build_cv.py --content cv/variants/risk.toml
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BULLET = chr(0xB7)  # Latin-1 middle dot, safe in a PDF text layer
GREY = RGBColor(0x59, 0x59, 0x59)
INK = RGBColor(0x1A, 0x1A, 0x1A)


def flat(text: str) -> str:
    return " ".join(text.split())


def load(path: Path) -> dict:
    """Read a content file, following ``base`` when it names one.

    Top-level keys replace entirely. That is blunt on purpose: a variant that
    quietly half-merged a list of skills with the master would produce claims
    nobody chose and nobody checked.
    """
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    base_ref = data.pop("base", None)
    if not base_ref:
        return data
    base_path = (path.parent / base_ref).resolve()
    merged = load(base_path)
    merged.update(data)
    return merged


CONTACT_KEYS = ("name", "location", "phone", "email", "eligibility_uk", "eligibility_au")


def with_contact(content: dict, root: Path) -> dict:
    """Overlay ``cv/contact.toml`` if it exists.

    Contact details are kept in their own file so the repository can be public
    without publishing a phone number. Missing file is not an error: the CV then
    renders with whatever content.toml says, which is exactly what CI or another
    clone would want.
    """
    path = root / "cv" / "contact.toml"
    if not path.is_file():
        return content
    with path.open("rb") as handle:
        contact = tomllib.load(handle)
    merged = dict(content)
    for key in CONTACT_KEYS:
        if contact.get(key):
            merged[key] = contact[key]
    return merged


# --- building blocks -----------------------------------------------------


def base_document() -> Document:
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(1.15)
    section.bottom_margin = Cm(1.15)
    section.left_margin = Cm(1.3)
    section.right_margin = Cm(1.3)

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(9)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.0
    return document


def heading(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(5)
    paragraph.paragraph_format.space_after = Pt(3)
    paragraph.paragraph_format.keep_with_next = True
    run = paragraph.add_run(text.upper())
    run.bold = True
    run.font.size = Pt(10)
    run.font.color.rgb = INK

    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), "B0B0B0")
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().append(borders)


def bullet(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    fmt = paragraph.paragraph_format
    fmt.left_indent = Cm(0.42)
    fmt.first_line_indent = Cm(-0.42)
    fmt.space_after = Pt(0.8)
    paragraph.add_run(f"{BULLET}  ")
    paragraph.add_run(text)


def role_head(document: Document, title: str, right: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(3.5)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.keep_with_next = True
    fmt = paragraph.paragraph_format
    fmt.tab_stops.add_tab_stop(Cm(18.0), WD_TAB_ALIGNMENT.RIGHT)
    paragraph.add_run(title).bold = True
    if right:
        run = paragraph.add_run(f"\t{right}")
        run.font.color.rgb = GREY


def sub_head(document: Document, left: str, right: str = "") -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(2)
    paragraph.paragraph_format.keep_with_next = True
    fmt = paragraph.paragraph_format
    fmt.tab_stops.add_tab_stop(Cm(18.0), WD_TAB_ALIGNMENT.RIGHT)
    run = paragraph.add_run(left)
    run.italic = True
    run.font.color.rgb = GREY
    if right:
        tail = paragraph.add_run(f"\t{right}")
        tail.italic = True
        tail.font.color.rgb = GREY


def plain(document: Document, text: str, *, italic: bool = False, size: float | None = None) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(1.5)
    run = paragraph.add_run(text)
    run.italic = italic
    if size:
        run.font.size = Pt(size)


# --- the document --------------------------------------------------------


def eligibility(content: dict, market: str) -> str:
    """The work-rights line, per market.

    A UK CV saying "you may work in the UK" is noise to a UK recruiter, so the
    UK line is empty and the row disappears. Australia gets its own line because
    "no sponsorship needed" is the single most useful thing on the page there.
    """
    return content.get(f"eligibility_{market}", "") or (
        content.get("eligibility", "") if market == "uk" else ""
    )


def render(content: dict, out_dir: Path, label: str = "", market: str = "uk") -> tuple[Path, Path]:
    doc = base_document()

    name = doc.add_paragraph()
    name.paragraph_format.space_after = Pt(1)
    run = name.add_run(content["name"])
    run.bold = True
    run.font.size = Pt(18)

    contact = doc.add_paragraph()
    contact.paragraph_format.space_after = Pt(1)
    run = contact.add_run(f"{content['location']}  |  {content['phone']}  |  {content['email']}")
    run.font.color.rgb = GREY

    eligibility_line = eligibility(content, market)
    if eligibility_line:
        line = doc.add_paragraph()
        line.paragraph_format.space_after = Pt(2)
        run = line.add_run(eligibility_line)
        run.font.size = Pt(9)
        run.font.color.rgb = GREY

    if content.get("summary"):
        heading(doc, "Profile")
        plain(doc, flat(content["summary"]))

    if content.get("education"):
        heading(doc, "Education")
        for entry in content["education"]:
            role_head(doc, entry["institution"], entry["dates"])
            sub_head(doc, entry["award"])
            for item in entry.get("bullets", []):
                bullet(doc, flat(item))
            if entry.get("note"):
                paragraph = doc.add_paragraph()
                paragraph.paragraph_format.space_before = Pt(2)
                run = paragraph.add_run(entry["note"])
                run.italic = True
                run.font.size = Pt(9)
                run.font.color.rgb = GREY

    if content.get("qualifications"):
        heading(doc, "Professional Qualifications")
        for block in content["qualifications"]:
            for item in block["items"]:
                bullet(doc, item)

    if content.get("experience"):
        heading(doc, "Professional Experience")
        for entry in content["experience"]:
            role_head(doc, entry["title"], entry["dates"])
            sub_head(doc, f"{entry['employer']}, {entry.get('place', '')}".strip(", "))
            for item in entry["bullets"]:
                bullet(doc, flat(item))

    if content.get("skills"):
        heading(doc, "Skills")
        for group in content["skills"]:
            paragraph = doc.add_paragraph()
            paragraph.paragraph_format.space_after = Pt(1.5)
            paragraph.paragraph_format.left_indent = Cm(0.42)
            paragraph.paragraph_format.first_line_indent = Cm(-0.42)
            run = paragraph.add_run(f"{group['name']}: ")
            run.bold = True
            paragraph.add_run(", ".join(group["items"]))

    if content.get("other"):
        heading(doc, "Other Experience")
        for entry in content["other"]:
            role_head(doc, entry["title"], entry["dates"])
            if entry.get("place"):
                sub_head(doc, entry["place"])
            for item in entry["bullets"]:
                bullet(doc, flat(item))

    if content.get("extracurricular"):
        heading(doc, "Extracurricular")
        for item in content["extracurricular"]["bullets"]:
            bullet(doc, flat(item))

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"Jed Dahlke CV {label}".strip()
    if market != "uk":
        stem = f"{stem} ({market.upper()})"
    docx_path = out_dir / f"{stem}.docx"
    doc.save(str(docx_path))
    txt_path = out_dir / f"{stem}.txt"
    txt_path.write_text(as_text(content, market), encoding="utf-8")
    return docx_path, txt_path


def as_text(content: dict, market: str = "uk") -> str:
    """The same content as plain text, for uploads that reject DOCX and for screening."""
    lines: list[str] = [content["name"], content["location"], f"{content['phone']} | {content['email']}"]
    eligibility_line = eligibility(content, market)
    if eligibility_line:
        lines.append(eligibility_line)
    lines.append("")

    if content.get("summary"):
        lines += ["PROFILE", flat(content["summary"]), ""]

    if content.get("education"):
        lines.append("EDUCATION")
        for entry in content["education"]:
            lines.append(f"{entry['institution']}, {entry.get('place', '')}  ({entry['dates']})")
            lines.append(entry["award"])
            lines += [f"{BULLET} {flat(item)}" for item in entry.get("bullets", [])]
            if entry.get("note"):
                lines.append(entry["note"])
            lines.append("")

    if content.get("qualifications"):
        lines.append("PROFESSIONAL QUALIFICATIONS")
        for block in content["qualifications"]:
            lines += [f"{BULLET} {item}" for item in block["items"]]
        lines.append("")

    if content.get("experience"):
        lines.append("PROFESSIONAL EXPERIENCE")
        for entry in content["experience"]:
            lines.append(f"{entry['title']}  ({entry['dates']})")
            lines.append(f"{entry['employer']}, {entry.get('place', '')}".strip(", "))
            lines += [f"{BULLET} {flat(item)}" for item in entry["bullets"]]
            lines.append("")

    if content.get("skills"):
        lines.append("SKILLS")
        for group in content["skills"]:
            lines.append(f"{group['name']}: {', '.join(group['items'])}")
        lines.append("")

    if content.get("other"):
        lines.append("OTHER EXPERIENCE")
        for entry in content["other"]:
            lines.append(f"{entry['title']}  ({entry['dates']})")
            if entry.get("place"):
                lines.append(entry["place"])
            lines += [f"{BULLET} {flat(item)}" for item in entry["bullets"]]
            lines.append("")

    if content.get("extracurricular"):
        lines.append("EXTRACURRICULAR")
        lines += [f"{BULLET} {flat(item)}" for item in content["extracurricular"]["bullets"]]

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the CV from content.toml")
    parser.add_argument("--content", default=None, help="one content file")
    parser.add_argument("--out", default="output")
    parser.add_argument("--all", action="store_true", help="master plus every file in cv/variants")
    parser.add_argument(
        "--market",
        default="both",
        choices=("uk", "au", "both"),
        help="which work-rights variant to render (default: both)",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    out_dir = root / args.out

    sources: list[Path] = []
    if args.all:
        sources.append(root / "cv" / "content.toml")
        sources.extend(sorted((root / "cv" / "variants").glob("*.toml")))
    elif args.content:
        sources.append(root / args.content)
    else:
        sources.append(root / "cv" / "content.toml")

    markets = ("uk", "au") if args.market == "both" else (args.market,)

    for source in sources:
        content = with_contact(load(source), root)
        label = content.get("label", "")
        for market in markets:
            docx_path, txt_path = render(content, out_dir, label, market)
            print(f"wrote {docx_path.name} and {txt_path.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())


