"""One-off copy pass: remove the two separators that read as generated chrome."""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

templates = Path(__file__).resolve().parents[1] / "src" / "jobhunter" / "templates"

for path in sorted(templates.glob("*.html")):
    text = path.read_text(encoding="utf-8")
    out = text.replace(" \u00b7 Jobhunter", " | Jobhunter")
    out = out.replace(" \u00b7 ", ", ")
    out = out.replace(" \u2192", "")
    if out != text:
        path.write_text(out, encoding="utf-8")
        print(f"updated {path.name}")
