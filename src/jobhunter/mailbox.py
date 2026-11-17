"""Reading a verification code out of your own inbox.

Creating a Workday account sends a one-time code. Typing it is the last manual
step in an otherwise unattended application, so this reads it instead: IMAP into
your mailbox, newest matching message first, pull the code out, hand it back.

It is deliberately narrow. It reads only what it needs for the code — headers and
the first text part — and it never leaves a message read. The password is a Gmail
*app password*, stored in the Credential Manager like every other secret here, not
your account password.

Untested against your mailbox until you run `jobhunter mail setup`. Until then no
code is fetched and the browser asks you to type one.
"""

from __future__ import annotations

import email
import imaplib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import credentials

DEFAULT_HOST = "imap.gmail.com"
SCOPE = "mail"
SLUG = "imap"


@dataclass(frozen=True)
class Code:
    value: str
    subject: str = ""
    sender: str = ""

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Link:
    url: str
    subject: str = ""
    sender: str = ""

    def __str__(self) -> str:
        return self.url


def address(config: dict) -> str:
    return str(
        (config.get("mail") or {}).get("address", "")
        or (config.get("profile") or {}).get("email", "")
    )


def host(config: dict) -> str:
    return str((config.get("mail") or {}).get("imap", DEFAULT_HOST))


def available(config: dict) -> bool:
    return bool(address(config)) and credentials.load(SCOPE, SLUG) is not None


def store_password(mail_address: str, app_password: str) -> None:
    credentials.store(SCOPE, SLUG, credentials.Secret(username=mail_address, password=app_password))


def forget_password() -> bool:
    return credentials.forget(SCOPE, SLUG)


def _password(config: dict) -> str:
    secret = credentials.load(SCOPE, SLUG)
    return secret.password if secret else ""


def connect(config: dict) -> imaplib.IMAP4_SSL:
    connection = imaplib.IMAP4_SSL(host(config), 993)
    connection.login(address(config), _password(config))
    return connection


def verify(config: dict, *, mail_address: str = "", app_password: str = "") -> str:
    """Try the credentials; return '' on success or the server's complaint."""
    try:
        connection = imaplib.IMAP4_SSL(host(config), 993)
        connection.login(mail_address or address(config), app_password or _password(config))
        connection.logout()
        return ""
    except imaplib.IMAP4.error as error:
        return str(error)
    except OSError as error:
        return f"could not reach {host(config)}: {error}"


CODE_WORDS = re.compile(r"\b(verification|verify|code|passcode|one[- ]time|otp|confirm)\b", re.I)
TOKEN = re.compile(r"\b([A-Z0-9]{5,8})\b")


def extract_code(text: str) -> str:
    """The most code-shaped token in a message, or ''.

    Codes vary by tenant: six digits at some, eight alphanumeric at others. So
    prefer a token on a line that talks about verification, require a digit so a
    stray word cannot pass as a code, and fall back to a bare six-digit number.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for line in lines:
        if not CODE_WORDS.search(line):
            continue
        for token in TOKEN.findall(line.upper()):
            if any(character.isdigit() for character in token):
                return token
    for line in lines:
        match = re.search(r"\b(\d{6})\b", line)
        if match:
            return match.group(1)
    return ""


def _body_text(message: email.message.Message, limit: int = 4000, *, keep_links: bool = False) -> str:
    """The readable text of a message.

    With ``keep_links`` the hrefs survive: an HTML mail that says "Reset your
    password" hides the only useful part in the anchor, and stripping tags
    throws it away.
    """
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", "replace")[:limit]
        for part in message.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                html = payload.decode(part.get_content_charset() or "utf-8", "replace")[:limit]
                return _html_text(html, keep_links=keep_links)
        return ""
    payload = message.get_payload(decode=True) or b""
    text = payload.decode(message.get_content_charset() or "utf-8", "replace")[:limit]
    if keep_links and message.get_content_type() == "text/html":
        return _html_text(text, keep_links=True)
    return text


def _html_text(html: str, *, keep_links: bool = False) -> str:
    if keep_links:
        html = re.sub(
            r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            lambda match: f"{re.sub(r'<[^>]+>', ' ', match.group(2))} {match.group(1)}",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return re.sub(r"<[^>]+>", " ", html)


LINK = re.compile(r"https?://[^\s<>\"')\]]+")

RESET_WORDS = ("reset", "password", "activate", "verify", "verification", "set-password", "token")


def extract_links(text: str, *, tenant: str = "") -> list[str]:
    """Links in a message, the ones that look like account mail first.

    Workday's reset mail names the tenant in the URL, and the useful link is the
    only one that says so; a footer full of social links must not win.
    """
    seen: list[str] = []
    for match in LINK.findall(text or ""):
        url = match.rstrip(".,;:)]")
        if url not in seen:
            seen.append(url)

    def rank(url: str) -> tuple[int, int]:
        lowered = url.lower()
        score = 0
        if tenant and tenant.lower() in lowered:
            score += 3
        if "myworkday" in lowered or "workday" in lowered:
            score += 2
        if any(word in lowered for word in RESET_WORDS):
            score += 1
        return (-score, len(url))

    return sorted(seen, key=rank)


def latest_link(
    config: dict,
    *,
    since: datetime | None = None,
    hints: tuple[str, ...] = ("workday",),
    tenant: str = "",
    wait_seconds: float = 0,
    interval: float = 6.0,
) -> Link | None:
    """Newest message carrying a link worth clicking, optionally waiting for it."""
    if not available(config):
        return None

    since = since or datetime.now() - timedelta(minutes=15)
    cutoff = (since - timedelta(days=1)).strftime("%d-%b-%Y")

    def extract(subject: str, sender: str, body: str) -> Link | None:
        links = extract_links(f"{subject}\n{body}", tenant=tenant)
        return Link(url=links[0], subject=subject, sender=sender) if links else None

    return _newest(config, cutoff, hints, extract, wait_seconds, interval)


def latest_code(
    config: dict,
    *,
    since: datetime | None = None,
    hints: tuple[str, ...] = ("workday",),
    wait_seconds: float = 0,
    interval: float = 6.0,
) -> Code | None:
    """Newest message with a code in it, optionally waiting for one to arrive."""
    if not available(config):
        return None

    since = since or datetime.now() - timedelta(minutes=10)
    # IMAP's SINCE is date-granular and in the server's language, so go back a day
    # and filter on time locally rather than depend on the server's clock.
    cutoff = (since - timedelta(days=1)).strftime("%d-%b-%Y")

    def extract(subject: str, sender: str, body: str) -> Code | None:
        code = extract_code(f"{subject}\n{body}")
        return Code(value=code, subject=subject, sender=sender) if code else None

    return _newest(config, cutoff, hints, extract, wait_seconds, interval)


def _newest(
    config: dict,
    cutoff: str,
    hints: tuple[str, ...],
    extract,
    wait_seconds: float,
    interval: float,
):
    """Poll until ``extract`` finds something, or the clock runs out."""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        found = _scan(config, cutoff, hints, extract)
        if found is not None:
            return found
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def _scan(config: dict, cutoff: str, hints: tuple[str, ...], extract):
    try:
        connection = connect(config)
    except (imaplib.IMAP4.error, OSError):
        return None
    try:
        connection.select("INBOX", readonly=True)
        status, data = connection.search(None, f'(SINCE "{cutoff}")')
        if status != "OK":
            return None
        numbers = (data[0] or b"").split()
        for number in reversed(numbers[-20:]):
            status, parts = connection.fetch(number, "(BODY.PEEK[])")
            if status != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            message = email.message_from_bytes(parts[0][1])
            subject = str(message.get("Subject", ""))
            sender = str(message.get("From", ""))
            haystack = f"{subject} {sender}".lower()
            if hints and not any(hint.lower() in haystack for hint in hints):
                continue
            found = extract(subject, sender, _body_text(message, keep_links=True))
            if found:
                return found
        return None
    finally:
        try:
            connection.logout()
        except Exception:  # noqa: BLE001
            pass
