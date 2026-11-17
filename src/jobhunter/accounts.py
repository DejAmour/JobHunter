"""Employer accounts: who you are at each Workday tenant, and where the password lives.

Workday is not one application form, it is a few hundred tenants that share a
codebase. Each one wants an account, and an account is per employer: the same
email and password at M&G will not work at abrdn.

So there is a small registry: one row per employer, holding the email (not a
secret) and a slug. The password goes to the Windows Credential Manager under
that slug. The signed-in browser state goes to a session file under that slug too.

None of this is committed: ``accounts.toml`` is gitignored, the session files are
gitignored, and the passwords are not in either.
"""

from __future__ import annotations

import csv
import re
import secrets
import string
from dataclasses import dataclass, replace
from pathlib import Path

from . import credentials

FILE = "accounts.toml"

# Characters that are easy to misread when you have to type a password into a
# browser by hand, which is exactly what happens when an account locks and the
# only route back is a reset form.
LOOKALIKES = "IlO0o1|`'\";:,.<>()[]{}~\\/"

LOWER = "".join(character for character in string.ascii_lowercase if character not in LOOKALIKES)
UPPER = "".join(character for character in string.ascii_uppercase if character not in LOOKALIKES)
DIGITS = "23456789"
SYMBOLS = "!@#$%^&*-_=+?"


def generate_password(length: int = 20) -> str:
    """A password that satisfies the rules employers actually print.

    Workday asks for eight characters, an uppercase, a lowercase, a numeric and
    a special. It also silently rejects some things nobody documents, so this
    avoids lookalike characters and anything a keyboard layout might mangle.

    One character from each pool is guaranteed, then the rest is random and the
    whole thing is shuffled: a fixed pattern like "letter, letter, symbol" is
    the kind of thing a bot-detector scores.
    """
    if length < 12:
        raise ValueError("refusing to generate shorter than 12 characters")
    pools = (UPPER, LOWER, DIGITS, SYMBOLS)
    characters = [secrets.choice(pool) for pool in pools]
    everything = "".join(pools)
    characters += [secrets.choice(everything) for _ in range(length - len(characters))]
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def export_csv(home: Path, path: Path) -> int:
    """Write every stored password to a CSV a manager can import.

    The format is Bitwarden's (name,url,username,password,notes), which KeePassXC
    and 1Password will also import with a column mapping. It is plaintext on
    purpose: the point is to hand it to a password manager once, then delete it.
    """
    rows = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "url", "username", "password", "notes"])
        for account in load(home):
            secret = credentials.load(account.ats, account.slug)
            if secret is None:
                continue
            writer.writerow(
                [
                    f"{account.firm} ({account.ats})",
                    f"https://{account.tenant}/" if account.tenant else "",
                    account.email,
                    secret.password,
                    "Employer login managed by jobhunter.",
                ]
            )
            rows += 1
    return rows


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:60] or "account"


@dataclass(frozen=True)
class Account:
    ats: str
    slug: str
    firm: str
    email: str
    tenant: str = ""

    def __str__(self) -> str:
        where = f" ({self.tenant})" if self.tenant else ""
        return f"{self.firm} [{self.ats}] {self.email}{where}"


def path_for(home: Path) -> Path:
    return home / FILE


def load(home: Path) -> list[Account]:
    path = path_for(home)
    if not path.is_file():
        return []
    import tomllib

    with path.open("rb") as handle:
        data = tomllib.load(handle)
    accounts = []
    for row in data.get("account", []):
        if not row.get("firm") or not row.get("ats"):
            continue
        accounts.append(
            Account(
                ats=str(row["ats"]),
                slug=str(row.get("slug") or slug(row["firm"])),
                firm=str(row["firm"]),
                email=str(row.get("email", "")),
                tenant=str(row.get("tenant", "")),
            )
        )
    return accounts


def save(home: Path, accounts: list[Account]) -> Path:
    path = path_for(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Employer accounts, one per ATS tenant.",
        "# Passwords are not here: they are in the Windows Credential Manager.",
        "",
    ]
    for account in accounts:
        lines.append("[[account]]")
        lines.append(f"ats = {_q(account.ats)}")
        lines.append(f"slug = {_q(account.slug)}")
        lines.append(f"firm = {_q(account.firm)}")
        lines.append(f"email = {_q(account.email)}")
        if account.tenant:
            lines.append(f"tenant = {_q(account.tenant)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _q(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def find(home: Path, firm: str, ats: str = "") -> Account | None:
    wanted = slug(firm)
    for account in load(home):
        if ats and account.ats != ats:
            continue
        if account.slug == wanted or slug(account.firm) == wanted:
            return account
    return None


def add(
    home: Path,
    firm: str,
    email: str,
    *,
    ats: str = "workday",
    password: str | None = None,
    tenant: str = "",
) -> Account:
    accounts = [a for a in load(home) if not (a.ats == ats and slug(a.firm) == slug(firm))]
    account = Account(ats=ats, slug=slug(firm), firm=firm, email=email, tenant=tenant)
    accounts.append(account)
    save(home, accounts)
    if password:
        credentials.store(ats, account.slug, credentials.Secret(username=email, password=password))
    return account


def upsert_tenant(home: Path, firm: str, tenant: str, *, ats: str = "workday") -> None:
    accounts = load(home)
    for index, account in enumerate(accounts):
        if account.ats == ats and slug(account.firm) == slug(firm):
            accounts[index] = replace(account, tenant=tenant)
            save(home, accounts)
            return


def remove(home: Path, firm: str, *, ats: str = "") -> bool:
    accounts = load(home)
    keep = [a for a in accounts if not (slug(a.firm) == slug(firm) and (not ats or a.ats == ats))]
    if len(keep) == len(accounts):
        return False
    for account in accounts:
        if account not in keep:
            credentials.forget(account.ats, account.slug)
            session_path(home, account).unlink(missing_ok=True)
    save(home, keep)
    return True


def has_password(account: Account) -> bool:
    return credentials.load(account.ats, account.slug) is not None


def password(account: Account) -> str:
    secret = credentials.load(account.ats, account.slug)
    return secret.password if secret else ""


def session_path(home: Path, account: Account) -> Path:
    return home / "sessions" / f"{account.ats}-{account.slug}.json"


def tenant_from_url(url: str) -> str:
    """``mg.wd3.myworkdayjobs.com`` -> ``mg.wd3``."""
    match = re.search(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com", (url or "").lower())
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def for_url(home: Path, url: str) -> Account | None:
    """The account whose tenant matches this job URL, if there is one."""
    tenant = tenant_from_url(url)
    if not tenant:
        return None
    tenant_host = tenant.split(".")[0]
    for account in load(home):
        if account.ats != "workday":
            continue
        if account.tenant and account.tenant.split(".")[0] == tenant_host:
            return account
    return None
