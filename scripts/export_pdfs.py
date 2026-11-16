"""Convert every DOCX in output/ to PDF and report page count + work-rights line."""

from __future__ import annotations

import sys
from pathlib import Path

import pdfplumber
from docx2pdf import convert

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

root = Path(__file__).resolve().parents[1]
out = root / "output"

for docx in sorted(out.glob("*.docx")):
    pdf = docx.with_suffix(".pdf")
    convert(str(docx), str(pdf))
    with pdfplumber.open(str(pdf)) as doc:
        text = doc.pages[0].extract_text() or ""
        pages = len(doc.pages)
    has_rights = "citizenship" in text.lower()
    print(f"{docx.stem:<34} pages={pages}  aus_rights={has_rights}")
