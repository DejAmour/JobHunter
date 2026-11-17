"""The fact sheet: asked once, then reused.

Filling the same eleven facts into every application form is the tedious part of
applying, and retyping them is where mistakes creep in — a postcode typo on one
application, an old phone number on the next.

So the facts live in ``~/.jobhunter/facts.toml``, written once by `jobhunter
facts`, and every command reads them. They are kept out of ``config.toml`` on
purpose: that file is hand-edited and commented, and a tool rewriting it would
eat the comments.

The voluntary-disclosure questions at the end are opt-in. They are never asked
unless you say yes, and they are never filled unless you said yes and provided a
value — but if you do want them remembered, remembering them is the point.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    hint: str = ""


QUESTIONS: tuple[Question, ...] = (
    Question("full_name", "Full name as it appears on your CV"),
    Question("email", "Email"),
    Question("phone", "Phone"),
    Question("address", "Address line 1"),
    Question("city", "City"),
    Question("postcode", "Postcode"),
    Question("country", "Country", "United Kingdom"),
    Question("location", "Location as you'd write it on a form", "City, Region"),
    Question("linkedin", "LinkedIn URL", "leave blank if you have none"),
    Question("website", "Portfolio or personal site", "optional"),
    Question("current_employer", "What you are doing now"),
    Question("current_title", "Current or most recent job title"),
    Question("notice_period", "Notice period / when you can start"),
    Question("salary_expectation", "Salary expectation", "blank leaves the field for you"),
    Question("highest_qualification", "Highest qualification"),
    Question("professional_qualifications", "Professional qualifications"),
    Question("willing_to_relocate", "Willing to relocate?"),
    Question("how_did_you_hear", "How did you hear about us? (default for forms)", "Company website"),
    Question("right_to_work", "Right-to-work statement for forms"),
    Question("needs_sponsorship", "Do you need sponsorship anywhere?", "yes/no"),
)

DISCLOSURES = (
    Question("gender", "Gender", "optional"),
    Question("ethnicity", "Ethnicity", "optional"),
    Question("veteran_status", "Veteran status", "optional"),
    Question("disability", "Disability status", "optional"),
)


def path_for(home: Path) -> Path:
    return home / "facts.toml"


def load(home: Path) -> dict:
    path = path_for(home)
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def save(home: Path, facts: dict) -> Path:
    path = path_for(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# Written by `jobhunter facts`. Read by every command.",
        "# Edit by hand if you prefer; rerun the command to change one answer.",
        "",
    ]
    answers = facts.get("answers", {})
    if answers:
        lines.append("[answers]")
        for key, value in answers.items():
            lines.append(f"{key} = {_toml(value)}")
        lines.append("")
    disclosures = facts.get("disclosures", {})
    if disclosures:
        lines += [
            "# Voluntary disclosure answers. Only used because you opted in.",
            "[disclosures]",
        ]
        for key, value in disclosures.items():
            lines.append(f"{key} = {_toml(value)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _toml(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def merge(config: dict, home: Path) -> dict:
    """Overlay the fact sheet on the config's answers.

    The fact sheet wins where it has a value: it was written later, deliberately,
    and by the person the answers describe.
    """
    facts = load(home)
    if not facts:
        return config
    merged = dict(config)
    answers = dict(config.get("answers") or {})
    answers.update({key: value for key, value in (facts.get("answers") or {}).items() if value})
    merged["answers"] = answers

    # The sponsorship fact is a legal declaration, and the engine reads it from
    # [candidate], so the fact sheet has to reach that table too rather than sit
    # in the answer bank unused.
    option = str(answers.get("needs_sponsorship", "")).strip().lower()
    if option.startswith(("yes", "y ", "true")):
        merged["candidate"] = {**(config.get("candidate") or {}), "needs_sponsorship": True}
    elif option.startswith(("no", "n ", "false")):
        merged["candidate"] = {**(config.get("candidate") or {}), "needs_sponsorship": False}

    if facts.get("disclosures"):
        merged["disclosures"] = facts["disclosures"]
    return merged


def ask(question: Question, current: str) -> str:
    suffix = f" [{current}]" if current else (f" ({question.hint})" if question.hint else "")
    try:
        reply = input(f"{question.prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit("cancelled; nothing written")
    return reply or current


def run(home: Path) -> int:
    existing = load(home)
    answers = dict(existing.get("answers", {}))

    print("The fact sheet. Press Enter to keep a value in brackets.\n")
    for question in QUESTIONS:
        answers[question.key] = ask(question, str(answers.get(question.key, "")))

    disclosures = dict(existing.get("disclosures", {}))
    print(
        "\nVoluntary disclosures (gender, ethnicity, veteran status, disability).\n"
        "These are only ever filled if you store them here. Skip them and they are\n"
        "left blank on every form."
    )
    try:
        wants = input("Store these for forms? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        wants = "n"
    if wants.startswith("y"):
        for question in DISCLOSURES:
            disclosures[question.key] = ask(question, str(disclosures.get(question.key, "")))
    else:
        disclosures = {}

    facts = {"answers": {k: v for k, v in answers.items() if v}, "disclosures": {k: v for k, v in disclosures.items() if v}}
    path = save(home, facts)
    print(f"\nwrote {len(facts['answers'])} answers to {path}")
    if facts["disclosures"]:
        print(f"and {len(facts['disclosures'])} disclosure answers")
    return 0
