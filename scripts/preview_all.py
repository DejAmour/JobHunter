"""Audit every PDF in output/ and render a preview of each."""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

root = Path(__file__).resolve().parents[1]
out = root / "output"

for pdf in sorted(out.glob("*.pdf")):
    doc = pymupdf.open(str(pdf))
    doc[0].get_pixmap(dpi=150).save(str(out / f"preview_{pdf.stem}.png"))
    print(f"{pdf.stem:<34} {len(doc)} page(s)")
