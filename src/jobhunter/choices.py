"""Choosing an option on a form, and remembering what was chosen.

Deterministic matching covers most of it: exact text, then a prefix. What is
left is genuine wording drift — your bank says "Company website", the tenant's
dropdown offers "Company Website", "Internet" or "Direct from employer".

Three deterministic answers, in order:

1. **Exact**, ignoring case and spacing.
2. **What was chosen here last time**, keyed by tenant and question, so the
   second application at an employer needs no judgement at all.
3. **A preference list** for questions where any close option is acceptable —
   how you heard about the role is the canonical one. Every option that could
   be picked is still one the form offered, and the list deliberately stops
   before "Other", which would usually open a free-text box.

Nothing here clicks anything. `choose` returns one of the strings it was given,
or None. That is the seam: a model could sit behind it later, choosing from the
same closed list, and the calling code would not change.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

FILE = "choices.json"

# Questions where a close option is as good as an exact one. Ordered: the first
# phrase that matches an offered option wins. No "Other" — it usually reveals a
# box asking you to say what, which is no longer equivalent.
PREFERENCES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (
        re.compile(r"how did you (?:hear|find)|where did you (?:hear|see)|source of application", re.I),
        (
            "company website",
            "company site",
            "careers page",
            "careers site",
            "company careers",
            "our website",
            "employer website",
            "direct",
            "internet",
        ),
    ),
)


def default_path() -> Path:
    base = os.environ.get("JOBHUNTER_HOME")
    return (Path(base) if base else Path.home() / ".jobhunter") / FILE


def load(path: Path | None = None) -> dict:
    target = Path(path) if path else default_path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict, path: Path | None = None) -> None:
    target = Path(path) if path else default_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def remember(tenant: str, question: str, option: str, *, path: Path | None = None) -> None:
    if not tenant or not question or not option:
        return
    data = load(path)
    data.setdefault(tenant, {})[question] = option
    save(data, path)


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


# Answer wording that a form spells differently. Only expands the *answer*, never
# overrides it, so a bank saying "MSc" can match "Master of Science (MSc)".
SYNONYMS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"\bmsc\b|\bmaster'?s?\b", re.I), ("msc", "master of science", "masters", "master")),
    (re.compile(r"\bbsc\b|\bbachelor'?s?\b", re.I), ("bsc", "bachelor of science", "bachelors", "bachelor")),
    (re.compile(r"\bba\b", re.I), ("ba", "bachelor of arts", "bachelors of arts")),
    (re.compile(r"\bmba\b", re.I), ("mba", "master of business administration")),
    (re.compile(r"\bphd\b|\bdphil\b", re.I), ("phd", "doctor of philosophy", "dphil")),
)


def _recorded(data: dict, tenant: str, question: str, options: list[str]) -> str | None:
    saved = (data.get(tenant) or {}).get(question)
    if not saved:
        return None
    for option in options:
        if _norm(option) == _norm(saved):
            return option
    # The tenant renamed the option; the memory is stale, not wrong.
    return None


def choose(
    question: str,
    options: list[str],
    answer: str,
    *,
    tenant: str = "",
    data: dict | None = None,
) -> tuple[str, str] | None:
    """One of ``options``, and why. None when nothing is close enough."""
    offered = [option for option in options if _norm(option)]
    if not offered:
        return None
    wanted = _norm(answer)

    if wanted:
        for option in offered:
            if _norm(option) == wanted:
                return option, "exact"
        for option in offered:
            if _norm(option).startswith(wanted):
                return option, "prefix"

    for pattern, variants in SYNONYMS:
        if not pattern.search(answer or ""):
            continue
        for variant in variants:
            for option in offered:
                if variant in _norm(option):
                    return option, f"synonym of the answer ({variant})"

    if tenant:
        remembered = _recorded(data if data is not None else load(), tenant, question, offered)
        if remembered is not None:
            return remembered, f"remembered for {tenant}"

    for pattern, phrases in PREFERENCES:
        if not pattern.search(question or ""):
            continue
        for phrase in phrases:
            for option in offered:
                if phrase in _norm(option):
                    return option, f"preference for this question ({phrase})"

    return None
