"""Write to a hiring team — a letter, not a broadcast.

The rules are enforced in code, not in a prompt, because a prompt is a
suggestion and this is the part where being wrong is expensive:

* only addresses actually read off a page are ``verified``;
* a guessed address is ``unverified`` and cannot be sent to without an override;
* dry run unless ``send`` is asked for *and* SMTP is configured;
* one recipient, one message, no CC or BCC, no attachments;
* a per-domain cap and a per-run cap;
* every draft carries an opt-out line naming where the address was found;
* no draft is produced when there is no concrete reason to write.

A draft that cannot name something specific about the role should not exist.
Sending one is how a domain gets burned.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage

from . import db

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MAILTO = re.compile(r"mailto:([^\"'?>\s]+)", re.IGNORECASE)

# Local parts that tend to mean "a person, not a robot", in rough priority order.
ROLE_HINTS: tuple[tuple[str, str], ...] = (
    ("talent", "talent"),
    ("recruit", "recruiter"),
    ("hiring", "hiring"),
    ("careers", "careers"),
    ("career", "careers"),
    ("jobs", "jobs"),
    ("hr", "hr"),
    ("people", "people"),
)

NO_REPLY = re.compile(r"^(no-?reply|donotreply|notifications?|automated|bounce)", re.IGNORECASE)


@dataclass(frozen=True)
class Contact:
    email: str
    company: str = ""
    name: str = ""
    role: str = ""
    source: str = ""
    verified: bool = False

    @property
    def local(self) -> str:
        return self.email.split("@", 1)[0]

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name else ""


@dataclass(frozen=True)
class Profile:
    name: str
    email: str
    signature: str = ""
    one_line: str = ""


@dataclass(frozen=True)
class Settings:
    host: str = ""
    port: int = 465
    user: str = ""
    password: str = field(default="", repr=False)
    from_address: str = ""
    per_domain_per_day: int = 2
    per_run: int = 20

    def configured(self) -> bool:
        return bool(self.host and self.user and self.password and self.from_address)


@dataclass(frozen=True)
class Draft:
    subject: str
    body: str


class OutreachRefused(Exception):
    """A send that the rules do not allow."""


def find_contacts(page_text: str, company: str = "", source: str = "") -> list[Contact]:
    """Addresses actually present in text we read. These are verified by definition."""
    found: list[Contact] = []
    seen: set[str] = set()
    for match in MAILTO.finditer(page_text):
        _add(found, seen, match.group(1), company, source, verified=True)
    for match in EMAIL.finditer(page_text):
        _add(found, seen, match.group(0), company, source, verified=True)
    return found


def guess_contacts(company: str, domain_name: str, person: str) -> list[Contact]:
    """Pattern guesses. Always unverified, and cannot be sent to by default.

    Guessing is offered because it is occasionally the only way in, but a guess
    is recorded as a guess and the sender refuses it without an explicit flag.
    """
    if not person or not domain_name:
        return []
    first, _, last = person.lower().partition(" ")
    patterns = [f"{first}.{last}", f"{first}{last}", f"{first}", f"{first[0]}{last}"]
    out: list[Contact] = []
    for local in dict.fromkeys(p for p in patterns if p.strip(".")):
        out.append(
            Contact(
                email=f"{local}@{domain_name}",
                company=company,
                name=person,
                role="guess",
                source="guessed pattern — unverified",
                verified=False,
            )
        )
    return out


def draft(contact: Contact, company: str, role: str, reason: str) -> Draft:
    """A short letter with one concrete reason and one ask."""
    clean = " ".join(reason.split())
    if len(clean) < 20:
        raise OutreachRefused(
            "no concrete reason to write: pass something specific from the posting "
            "(a project, a product, a problem). A letter without one is spam."
        )
    greeting = f"Hi {contact.first_name}," if contact.first_name else "Hello,"
    subject = f"{role} at {company} — a question"
    body = "\n".join(
        [
            greeting,
            "",
            f"I'm applying for the {role} role at {company}, and I wanted to write to you directly rather than only through the form.",
            "",
            f"{clean}",
            "",
            "If you're not the right person for this, could you point me to whoever owns hiring for the team? Either way, thanks for reading.",
            "",
            "Best,",
            "",
        ]
    )
    return Draft(subject=subject, body=body)


def compose(profile: Profile, contact: Contact, letter: Draft, source_note: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = profile.email
    message["To"] = contact.email
    message["Subject"] = letter.subject
    body = "\n".join(line for line in (letter.body, profile.signature, profile.one_line) if line)
    opt_out = (
        "\n\n--\n"
        f"I found this address {source_note}. Reply \"unsubscribe\" and I won't write again."
    )
    message.set_content(body.rstrip() + opt_out)
    return message


def send(
    connection,
    settings: Settings,
    profile: Profile,
    contact: Contact,
    letter: Draft,
    *,
    job_id: int | None = None,
    source_note: str = "published on the company's own site",
    dry_run: bool = True,
    allow_unverified: bool = False,
    run_count: int = 0,
) -> str:
    """Send one letter, or say why not. Returns the outcome word."""
    if not contact.verified and not allow_unverified:
        raise OutreachRefused(
            f"{contact.email} is unverified ({contact.source}); find the address on a page, "
            "or pass allow_unverified if you are certain"
        )
    if NO_REPLY.match(contact.local):
        raise OutreachRefused(f"{contact.email} looks automated; a human will not read it")
    if run_count >= settings.per_run:
        raise OutreachRefused(f"per-run cap of {settings.per_run} reached")
    if db.sent_today_to_domain(connection, db.domain(contact.email)) >= settings.per_domain_per_day:
        raise OutreachRefused(
            f"already wrote to {db.domain(contact.email)} today; the cap is {settings.per_domain_per_day}"
        )

    message = compose(profile, contact, letter, source_note)

    if dry_run:
        contact_id = db.record_contact(
            connection,
            email=contact.email,
            company=contact.company,
            name=contact.name,
            role=contact.role,
            source=contact.source,
            verified=contact.verified,
        )
        db.record_outreach(
            connection,
            contact_id=contact_id,
            job_id=job_id,
            subject=letter.subject,
            body=message.get_content(),
            status="dry-run",
            dry_run=True,
        )
        return "dry-run"

    if not settings.configured():
        raise OutreachRefused("SMTP is not configured; nothing was sent")

    context = ssl.create_default_context()
    if settings.port == 465:
        with smtplib.SMTP_SSL(settings.host, settings.port, context=context, timeout=30) as server:
            server.login(settings.user, settings.password)
            server.send_message(message)
    else:
        with smtplib.SMTP(settings.host, settings.port, timeout=30) as server:
            server.starttls(context=context)
            server.login(settings.user, settings.password)
            server.send_message(message)

    contact_id = db.record_contact(
        connection,
        email=contact.email,
        company=contact.company,
        name=contact.name,
        role=contact.role,
        source=contact.source,
        verified=contact.verified,
    )
    db.record_outreach(
        connection,
        contact_id=contact_id,
        job_id=job_id,
        subject=letter.subject,
        body=message.get_content(),
        status="sent",
        dry_run=False,
    )
    return "sent"


def _add(found: list[Contact], seen: set[str], email: str, company: str, source: str, *, verified: bool) -> None:
    address = email.strip().strip(".,;:()<>").lower()
    if not EMAIL.fullmatch(address) or address in seen:
        return
    seen.add(address)
    local = address.split("@", 1)[0]
    role = next((label for hint, label in ROLE_HINTS if hint in local), "person")
    found.append(Contact(email=address, company=company, role=role, source=source, verified=verified))
