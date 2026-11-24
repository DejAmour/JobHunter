"""Command line.

Deliberately small. The interesting logic lives in ``screening`` and
``outreach``; this only parses arguments, loads config, and prints.

Defaults are the safe ones: ``send`` is a dry run unless ``--send`` is passed,
and nothing is written to an employer without a reason that names something
specific.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

from . import cv, db, discover as discovery, outreach, screening


def home() -> Path:
    override = os.environ.get("JOBHUNTER_HOME")
    return Path(override) if override else Path.home() / ".jobhunter"


def load(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def workday_url(store: Path, firm: str, explicit: str = "") -> str:
    """Newest Workday posting stored for this employer, or the one given."""
    if explicit:
        return explicit
    with db.connect(store) as connection:
        row = connection.execute(
            "SELECT url FROM jobs WHERE company LIKE ? AND url LIKE '%myworkdayjobs%' "
            "ORDER BY id DESC LIMIT 1",
            (f"%{firm}%",),
        ).fetchone()
    return row["url"] if row else ""


def settings(config: dict) -> outreach.Settings:
    smtp = config.get("smtp", {})
    limits = config.get("limits", {})
    return outreach.Settings(
        host=str(smtp.get("host", "")),
        port=int(smtp.get("port", 465)),
        user=str(smtp.get("user", "")),
        password=os.environ.get("JOBHUNTER_SMTP_PASSWORD", ""),
        from_address=str(smtp.get("from", "")),
        per_domain_per_day=int(limits.get("per_domain_per_day", 2)),
        per_run=int(limits.get("per_run", 20)),
    )


def profile(config: dict) -> outreach.Profile:
    block = config.get("profile", {})
    return outreach.Profile(
        name=str(block.get("name", "")),
        email=str(block.get("email", "")),
        signature=str(block.get("signature", "")),
        one_line=str(block.get("one_line", "")),
    )


def candidate(config: dict) -> screening.Candidate:
    block = config.get("candidate", {})
    return screening.Candidate(
        needs_sponsorship=bool(block.get("needs_sponsorship", False)),
        salary_floor=int(block["salary_floor"]) if block.get("salary_floor") is not None else None,
        currency=str(block.get("currency", "GBP")),
        remote_only=bool(block.get("remote_only", False)),
        years_experience=float(block["years_experience"]) if block.get("years_experience") is not None else None,
        clearances=tuple(block.get("clearances", [])),
        right_to_work=block.get("right_to_work"),
    )


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252, which turns £ and – into question marks.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(prog="jobhunter", description="Find, screen and apply to jobs.")
    parser.add_argument("--config", default=None, help="path to config.toml")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="report what is configured")
    commands.add_parser("db-init", help="create the store")
    commands.add_parser("facts", help="answer the fact sheet once, reused by every form")

    discover_parser = commands.add_parser("discover", help="read employer job boards and screen what is there")
    discover_parser.add_argument("--firms", default=None, help="path to firms.toml")
    discover_parser.add_argument("--firm", default="", help="only this employer (substring)")
    discover_parser.add_argument("--ats", default="", help="only this board type, e.g. workday")
    discover_parser.add_argument("--dry-run", action="store_true", help="fetch and filter, write nothing")
    discover_parser.add_argument("--max-detail", type=int, default=25, help="descriptions to fetch per employer")
    discover_parser.add_argument("--quiet", action="store_true")

    probe_parser = commands.add_parser("probe", help="find out which job board an employer uses")
    probe_parser.add_argument("--domain", action="append", default=[], help="e.g. schroders.com (repeatable)")
    probe_parser.add_argument("--file", default=None, help="a text file of domains, one per line")
    probe_parser.add_argument("--firms", default=None, help="firms.toml to append verified entries to")
    probe_parser.add_argument("--write", action="store_true", help="append verified entries to firms.toml")

    record_parser = commands.add_parser(
        "record", help="save a live application form as a replayable test fixture"
    )
    record_parser.add_argument("--url", default="", help="the posting or form URL")
    record_parser.add_argument("--job", type=int, default=0, help="record the URL of a stored job")
    record_parser.add_argument("--ats", default="", help="board type, if the URL does not say")
    record_parser.add_argument("--out", default="", help="fixture folder (default: tests/fixtures/recorded)")
    record_parser.add_argument("--click", default="", help="text on the control that reveals the form")
    record_parser.add_argument(
        "--wait", type=float, default=15, help="seconds to wait for a single-page form to render"
    )
    record_parser.add_argument("--headless", action="store_true", help="do not show the browser window")

    serve_parser = commands.add_parser("serve", help="run the local dashboard")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--host", default="127.0.0.1")

    import_parser = commands.add_parser("import", help="read a page you saved yourself (LinkedIn, Indeed)")
    import_parser.add_argument("--file", required=True)
    import_parser.add_argument("--kind", default="auto", choices=("auto", "linkedin", "indeed", "jsonld"))
    import_parser.add_argument("--dry-run", action="store_true")

    login_parser = commands.add_parser("login", help="capture a signed-in browser session")
    login_parser.add_argument("--site", default="indeed", choices=("indeed", "linkedin", "workday"))
    login_parser.add_argument("--firm", default="", help="which employer, for --site workday")
    login_parser.add_argument("--url", default="", help="start from this job URL instead of the newest in the store")
    login_parser.add_argument("--create", action="store_true", help="create the account instead of signing in")

    account_parser = commands.add_parser("account", help="employer accounts: the email here, the password in Windows")
    account_commands = account_parser.add_subparsers(dest="account_command")
    account_add = account_commands.add_parser("add", help="register an employer account")
    account_add.add_argument("--firm", required=True)
    account_add.add_argument("--email", default="", help="defaults to profile.email")
    account_add.add_argument("--ats", default="workday")
    account_add.add_argument(
        "--generate",
        action="store_true",
        help="generate a strong password instead of typing one (shown once)",
    )
    account_add.add_argument("--length", type=int, default=20, help="length for --generate")
    account_commands.add_parser("list", help="show what is registered")
    account_show = account_commands.add_parser("show", help="print the stored password for one employer")
    account_show.add_argument("--firm", required=True)
    account_export = account_commands.add_parser(
        "export", help="write every stored password to a CSV a password manager can import"
    )
    account_export.add_argument("--out", default="", help="defaults to ~/.jobhunter/passwords.csv")
    account_import = account_commands.add_parser(
        "import", help="take one employer's password from a manager's CSV export"
    )
    account_import.add_argument("--csv", required=True, help="the export from Chrome or your manager")
    account_import.add_argument("--firm", required=True)
    account_remove = account_commands.add_parser("remove", help="forget an employer account")
    account_remove.add_argument("--firm", required=True)
    account_recover = account_commands.add_parser(
        "recover", help="walk a locked or forgotten Workday account back to a sign-in"
    )
    account_recover.add_argument("--firm", required=True)
    account_recover.add_argument("--url", default="", help="a Workday page to start from")
    account_recover.add_argument("--wait", type=float, default=180, help="seconds to wait for the email")

    mail_parser = commands.add_parser("mail", help="read verification codes from your inbox")
    mail_commands = mail_parser.add_subparsers(dest="mail_command")
    mail_setup = mail_commands.add_parser("setup", help="store a Gmail app password")
    mail_setup.add_argument("--address", default="", help="defaults to profile.email")
    mail_commands.add_parser("check", help="show whether a code can be read right now")
    mail_link = mail_commands.add_parser(
        "link", help="find the newest verification or password-reset link"
    )
    mail_link.add_argument("--firm", default="", help="narrow to this employer's mail")
    mail_link.add_argument("--wait", type=float, default=0, help="seconds to wait for it to arrive")
    mail_link.add_argument("--open", action="store_true", help="open it in your browser")

    refresh_parser = commands.add_parser("refresh", help="read the full posting for rows that only stored a summary")
    refresh_parser.add_argument("--limit", type=int, default=40)
    refresh_parser.add_argument("--decision", default="apply", choices=("apply", "ask", "exclude", ""))
    refresh_parser.add_argument("--country", default="", choices=("uk", "au", ""))
    refresh_parser.add_argument("--dry-run", action="store_true")

    apply_parser = commands.add_parser("apply", help="prepare an application pack for one posting")
    apply_parser.add_argument("--job", type=int, required=True, help="job id from the dashboard or jobs list")
    apply_parser.add_argument("--open", action="store_true", help="open the posting in your browser")
    apply_parser.add_argument("--no-record", action="store_true", help="write the pack but do not track it")

    fill_parser = commands.add_parser("fill", help="fill an application form and stop before submitting")
    fill_parser.add_argument("--job", type=int, required=True)
    fill_parser.add_argument("--submit", action="store_true", help="press submit (default: stop for review)")
    fill_parser.add_argument("--headless", action="store_true", help="do not show the browser window")
    fill_parser.add_argument("--url", default="", help="override the posting URL (useful for fixtures)")
    fill_parser.add_argument("--ats", default="", help="force a board type (greenhouse, lever, workable, ashby) instead of detecting it")
    fill_parser.add_argument("--advance", action="store_true", help="on Workday, walk to the next step after filling")
    fill_parser.add_argument(
        "--hold",
        action="store_true",
        help="leave the browser open so you can finish and submit it yourself",
    )
    fill_parser.add_argument("--firm", default="", help="which employer account to use, if the URL does not say")
    fill_parser.add_argument(
        "--create-account",
        dest="create_account",
        action="store_true",
        default=None,
        help="create the employer account if it does not exist yet (default: only on the first run)",
    )
    fill_parser.add_argument("--no-create-account", dest="create_account", action="store_false")

    screen_parser = commands.add_parser("screen", help="screen a posting before applying")
    screen_parser.add_argument("--file", required=True, help="the posting text")
    screen_parser.add_argument("--cv", default=None, help="the CV text (default: profile.cv)")

    cv_parser = commands.add_parser("cv-audit", help="read a CV the way an ATS parser reads it")
    cv_parser.add_argument("--file", default=None, help="CV path (pdf, docx, txt); default: profile.cv")
    cv_parser.add_argument("--terms", default=None, help="comma-separated keywords to check (default: search.terms)")

    contacts_parser = commands.add_parser("contacts", help="find addresses in a page you saved")
    contacts_parser.add_argument("--file", required=True)
    contacts_parser.add_argument("--company", default="")
    contacts_parser.add_argument("--save", action="store_true", help="record them in the store")

    draft_parser = commands.add_parser("draft", help="draft a letter")
    draft_parser.add_argument("--to", required=True)
    draft_parser.add_argument("--company", required=True)
    draft_parser.add_argument("--role", required=True)
    draft_parser.add_argument("--reason", required=True, help="something specific from the posting")

    send_parser = commands.add_parser("send", help="send a drafted letter (dry run unless --send)")
    send_parser.add_argument("--to", required=True)
    send_parser.add_argument("--company", required=True)
    send_parser.add_argument("--role", required=True)
    send_parser.add_argument("--reason", required=True)
    send_parser.add_argument("--source-note", default="published on the company's own site")
    send_parser.add_argument("--verified", action="store_true", help="the address was read off a page")
    send_parser.add_argument("--send", action="store_true", help="actually transmit")

    args = parser.parse_args(argv)
    config_path = Path(args.config) if args.config else home() / "config.toml"
    config = load(config_path)
    from . import facts as facts_module

    config = facts_module.merge(config, home())
    store = home() / "jobs.db"

    if args.command == "doctor":
        print(f"config: {config_path} ({'found' if config_path.is_file() else 'missing'})")
        print(f"store:  {store} ({'found' if store.is_file() else 'missing'})")
        who = profile(config)
        print(f"profile: {who.name or '(no name)'} <{who.email or 'no email'}>")
        smtp = settings(config)
        print(f"smtp:   {'configured' if smtp.configured() else 'not configured (sending is dry run only)'}")
        print(f"caps:   {smtp.per_run} per run, {smtp.per_domain_per_day} per domain per day")
        cv_setting = config.get("profile", {}).get("cv", "")
        print(f"cv:     {cv_setting or '(none set)'}")
        print(f"plugins: {len(list((Path(__file__).parents[2] / 'vendor' / 'aihawk' / 'plugins').glob('*.md')))} vendored")
        return 0

    if args.command == "facts":
        from . import facts

        return facts.run(home())

    if args.command == "db-init":
        db.init(store)
        print(f"created {store}")
        return 0

    if args.command == "discover":
        firms_path = Path(
            args.firms
            or config.get("search", {}).get("firms_path", "")
            or Path(__file__).resolve().parents[2] / "firms.toml"
        )
        if not firms_path.is_file():
            print(f"no firm registry at {firms_path}")
            return 2
        registry = load(firms_path).get("firms", [])
        if not registry:
            print(f"{firms_path} has no [[firms]] entries")
            return 2

        cv_path = config.get("profile", {}).get("cv", "")
        cv_text = ""
        if cv_path and Path(cv_path).is_file():
            if Path(cv_path).suffix.lower() in {".pdf", ".docx"}:
                cv_text, _, _ = cv.extract(Path(cv_path))
            else:
                cv_text = read(cv_path)

        db.init(store)
        print(f"{len(registry)} employer boards, cv: {Path(cv_path).name if cv_path else 'none'}")
        results = discovery.run(
            config,
            registry,
            store=store,
            cv_text=cv_text,
            candidate=candidate(config),
            dry_run=args.dry_run,
            only=args.firm,
            only_ats=args.ats,
            max_detail=args.max_detail,
            verbose=not args.quiet,
        )
        print()
        print(discovery.summary(results))
        return 0

    if args.command == "probe":
        from . import probe

        domains = list(args.domain)
        if args.file:
            domains += [
                line.strip()
                for line in Path(args.file).read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            ]
        if not domains:
            print("nothing to probe: pass --domain or --file")
            return 2

        print(f"probing {len(domains)} domains")
        found = probe.run(domains)
        print(f"\n{len(found)} boards answered with jobs")
        for entry in found:
            print()
            print(probe.as_toml(entry))

        if args.write and found:
            firms_path = Path(
                args.firms
                or config.get("search", {}).get("firms_path", "")
                or Path(__file__).resolve().parents[2] / "firms.toml"
            )
            added = probe.append_to(firms_path, found)
            print(f"\nwrote {added} new entries to {firms_path}")
        return 0

    if args.command == "record":
        from . import record

        url = args.url
        ats = args.ats
        if args.job:
            db.init(store)
            with db.connect(store) as connection:
                row = connection.execute(
                    "SELECT url FROM jobs WHERE id = ?", (args.job,)
                ).fetchone()
            if row is None:
                print(f"no job {args.job}")
                return 2
            url = url or (row["url"] or "")
        if not url:
            print("nothing to record: pass --url or --job")
            return 2

        ats = ats or record.detect_ats(url)
        out = Path(args.out) if args.out else (
            Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "recorded" / (ats or "unknown")
        )
        print(f"recording {url}")
        meta = record.capture(
            url,
            ats=ats,
            out_dir=out,
            headless=args.headless,
            click=args.click,
            wait_for_controls=args.wait,
        )
        print(f"  ats:      {meta['ats'] or 'unknown'}")
        print(f"  final:    {meta['final_url']}")
        print(f"  fixture:  {meta['path']}")
        print(f"  controls: {meta['control_count']}")
        for note in meta["notes"]:
            print(f"  note:     {note}")
        for control in meta["controls"]:
            print(f"    {record.inventory_line(control)}")
        if meta["control_count"] == 0:
            print("  buttons seen:")
            for button in meta["buttons"][:20]:
                print(f"    {button['tag']:<7} {button['text'][:40]:<42} {button['href'][:50]}")
            return 1
        return 0

    if args.command == "refresh":
        from . import enrich

        db.init(store)
        results = enrich.run(
            config,
            store=store,
            limit=args.limit,
            decision=args.decision,
            country=args.country,
            dry_run=args.dry_run,
        )
        print()
        print(enrich.summary(results))
        return 0

    if args.command == "apply":
        from . import apply as apply_pack

        db.init(store)
        cv_path = config.get("profile", {}).get("cv", "")
        cv_text = ""
        if cv_path and Path(cv_path).is_file():
            cv_text = cv.extract(Path(cv_path))[0] if Path(cv_path).suffix.lower() in {".pdf", ".docx"} else read(cv_path)
        apply_pack.prepare(
            store,
            Path(__file__).resolve().parents[2],
            args.job,
            config,
            cv_text=cv_text,
            record_it=not args.no_record,
            open_it=args.open,
        )
        return 0

    if args.command == "fill":
        from . import apply as apply_pack
        from . import fill as form_fill

        db.init(store)
        root = Path(__file__).resolve().parents[2]
        cv_path = config.get("profile", {}).get("cv", "")
        cv_text = ""
        if cv_path and Path(cv_path).is_file():
            cv_text = cv.extract(Path(cv_path))[0] if Path(cv_path).suffix.lower() in {".pdf", ".docx"} else read(cv_path)

        pack = apply_pack.build(
            db.connect(store),
            args.job,
            config,
            root=root,
            cv_text=cv_text,
        )
        cv_file = root / "output" / pack.cv_path
        values = form_fill.values_from(config, cv_path=str(cv_file), org=pack.job["company"], market=pack.market)
        url = args.url or pack.job["url"]

        print(f"{pack.job['title']} — {pack.job['company']}")
        print(f"  cv:  {cv_file.name}{'' if cv_file.is_file() else '  (missing!)'}")
        print(f"  url: {url}")

        from . import accounts as accounts_module
        from . import mailbox

        account = None
        if args.firm:
            account = accounts_module.find(home(), args.firm, ats="workday")
            if account is None:
                print(
                    f"  account: no registration for {args.firm!r}; "
                    f"run `jobhunter account add --firm \"{args.firm}\"` first"
                )
        else:
            account = accounts_module.for_url(home(), url)

        create_account = args.create_account
        if account is not None and create_account is None:
            # First run at this employer: the account probably does not exist yet.
            create_account = not accounts_module.session_path(home(), account).is_file()

        fetch_code = None
        if mailbox.available(config):
            from datetime import datetime

            started = datetime.now()

            def fetch_code():
                found = mailbox.latest_code(config, since=started, wait_seconds=120)
                return found.value if found else ""

        if account is not None:
            known = "" if accounts_module.has_password(account) else "  (no password stored)"
            print(f"  account: {account.firm} <{account.email}>{known}")

        report = form_fill.run(
            url,
            values,
            ats=args.ats,
            ctx=form_fill.context_for(config, pack),
            submit=args.submit,
            advance=args.advance,
            # A window you cannot see is not a window you can finish an
            # application in, so --hold implies headed.
            headless=args.headless and not args.hold,
            hold=args.hold,
            screenshot_dir=root / "output" / "applications" / apply_pack._slug(
                f"{pack.job['company']}-{pack.job['title']}"
            ),
            account=account,
            home=home(),
            create_account=bool(create_account),
            on_code=fetch_code,
        )
        print()
        print(report.summary())

        # Record the attempt against the application, so the ledger says a form
        # was filled even when nothing was sent. The dashboard's runs use the
        # same writer, so a run started from the web and one started from the
        # terminal leave identical evidence.
        from . import runs

        runs.record_attempt(store, args.job, report, variant=pack.variant, market=pack.market)
        return 0 if not report.error else 1

    if args.command == "login":
        from . import sessions

        if args.site == "workday":
            from . import accounts as accounts_module

            account = accounts_module.find(home(), args.firm, ats="workday") if args.firm else None
            if account is None:
                print('which employer? try `jobhunter login --site workday --firm "M&G"`')
                return 2
            url = workday_url(store, account.firm, args.url)
            if not url:
                print("no Workday posting found for that employer; pass --url")
                return 2
            return sessions.login_workday(home(), config, account, url=url, create=args.create)
        return sessions.login(args.site, home())

    if args.command == "account":
        from . import accounts as accounts_module
        from . import credentials

        if args.account_command in (None, "list"):
            known = accounts_module.load(home())
            if not known:
                print("no employer accounts yet: `jobhunter account add --firm \"M&G\"`")
                return 0
            for account in known:
                mark = "password stored" if accounts_module.has_password(account) else "NO PASSWORD"
                state = accounts_module.session_path(home(), account)
                signed_in = "signed in" if state.is_file() else "not signed in yet"
                print(f"{account}  {mark}, {signed_in}")
            return 0

        if args.account_command == "add":
            import getpass

            email = args.email or str(config.get("profile", {}).get("email", ""))
            if not email:
                email = input("email for this employer: ").strip()
            if not credentials.available():
                print("no credential store on this machine; refusing to write a password to disk")
                return 2

            generated = ""
            if getattr(args, "generate", False):
                generated = accounts_module.generate_password(getattr(args, "length", 20))
                print(f"generated a {len(generated)}-character password for {email} at {args.firm}")
                print("this is the only time it is shown. `jobhunter account show` will print it again.")
                first = generated
            else:
                print(f"password for {email} at {args.firm}")
                print("it goes to the Windows Credential Manager, not to a file in this repository.")
                first = getpass.getpass("password: ")
                second = getpass.getpass("again: ")
                if not first:
                    print("empty password; nothing stored")
                    return 2
                if first != second:
                    print("the two did not match; nothing stored")
                    return 2

            account = accounts_module.add(home(), args.firm, email, ats=args.ats, password=first)
            print(f"registered {account}")
            if generated:
                print(f"  password: {generated}")
                print("  paste it into the employer's form if you are creating the account by hand,")
                print("  and into your password manager when you next sync it.")
            return 0

        if args.account_command == "show":
            account = accounts_module.find(home(), args.firm)
            if account is None:
                print(f"no account for {args.firm!r}: `jobhunter account add --firm \"{args.firm}\"`")
                return 2
            secret = credentials.load(account.ats, account.slug)
            if secret is None:
                print(f"{account}: no password stored")
                return 2
            print(f"{account}")
            print(f"  password: {secret.password}")
            return 0

        if args.account_command == "export":
            out = Path(args.out) if args.out else home() / "passwords.csv"
            rows = accounts_module.export_csv(home(), out)
            print(f"wrote {rows} password(s) to {out}")
            print("this file is plaintext. Import it into your password manager, then delete it.")
            return 0 if rows else 1

        if args.account_command == "import":
            source = Path(args.csv).expanduser()
            if not source.is_file():
                print(f"no such file: {source}")
                return 2
            taken = accounts_module.import_password(home(), source, args.firm, ats=args.ats)
            if taken is None:
                print(f"no row for {args.firm!r} in {source}")
                print("nothing stored. check the account exists first:")
                print(f'  jobhunter account add --firm "{args.firm}" --generate')
                return 1
            account, label = taken
            print(f"imported {account}")
            print(f"  from row: {label}")
            print("  delete the CSV now: it holds every password your manager has exported.")
            return 0

        if args.account_command == "recover":
            from . import sessions

            account = accounts_module.find(home(), args.firm, ats="workday")
            if account is None:
                print(f'no Workday account for {args.firm!r}: `jobhunter account add --firm "{args.firm}"`')
                return 2
            url = workday_url(store, account.firm, args.url)
            if not url:
                print("no Workday posting found for that employer; pass --url")
                return 2
            return sessions.recover_workday(
                home(), config, account, url=url, wait_seconds=args.wait
            )

        if args.account_command == "remove":
            if accounts_module.remove(home(), args.firm):
                print(f"forgot {args.firm}: registration, password and session")
                return 0
            print(f"no account registered for {args.firm!r}")
            return 2

    if args.command == "mail":
        from . import mailbox

        if args.mail_command == "setup":
            import getpass

            address = args.address or str(config.get("profile", {}).get("email", ""))
            print("This is a Gmail *app password*, not your account password.")
            print("Create one at https://myaccount.google.com/apppasswords (needs 2-step verification).")
            password = getpass.getpass("app password: ")
            if not password:
                print("nothing stored")
                return 2
            problem = mailbox.verify(config, mail_address=address, app_password=password)
            if problem:
                print(f"Gmail refused it: {problem}")
                return 1
            mailbox.store_password(address, password)
            print(f"stored; codes for {address} can now be read automatically")
            return 0

        if args.mail_command in (None, "check"):
            if not mailbox.available(config):
                print("no app password stored: `jobhunter mail setup`")
                return 0
            problem = mailbox.verify(config)
            print(f"{mailbox.address(config)}: {'ok' if not problem else problem}")
            return 0 if not problem else 1

        if args.mail_command == "link":
            if not mailbox.available(config):
                print("no app password stored: `jobhunter mail setup`")
                print("until then, the reset link has to be clicked in your inbox by hand.")
                return 2
            hints: tuple[str, ...] = ("workday",)
            if args.firm:
                hints = ("workday", args.firm.lower())
            print(f"looking for the newest verification or reset mail{f' from {args.firm}' if args.firm else ''}")
            found = mailbox.latest_link(
                config, hints=hints, wait_seconds=args.wait, tenant=args.firm
            )
            if found is None:
                print("nothing found in the last 15 minutes")
                return 1
            print(f"  subject: {found.subject}")
            print(f"  from:    {found.sender}")
            print(f"  link:    {found.url}")
            if args.open:
                import webbrowser

                webbrowser.open(found.url)
                print("  opened in your browser")
            return 0

    if args.command == "import":
        from . import import_cmd

        db.init(store)
        return import_cmd.run(
            config,
            args.file,
            store=store,
            kind=args.kind,
            dry_run=args.dry_run,
            candidate=candidate(config),
        )

    if args.command == "serve":
        from .web import serve

        return serve(store, config_path, host=args.host, port=args.port)

    if args.command == "screen":
        posting = read(args.file)
        cv_path = args.cv or config.get("profile", {}).get("cv", "")
        cv_text = ""
        if cv_path and Path(cv_path).is_file():
            if Path(cv_path).suffix.lower() in {".pdf", ".docx"}:
                cv_text, _, _ = cv.extract(Path(cv_path))
            else:
                cv_text = read(cv_path)
        report = screening.screen(posting, cv_text, candidate(config))
        print(report.summary())
        return 0 if report.decision == "apply" else 1

    if args.command == "cv-audit":
        path = args.file or config.get("profile", {}).get("cv", "")
        if not path:
            print("no CV: pass --file or set profile.cv")
            return 2
        if args.terms:
            terms = tuple(t.strip() for t in args.terms.split(",") if t.strip())
        else:
            terms = tuple(config.get("search", {}).get("terms", []))
        result = cv.audit(Path(path), terms)
        print(result.report())
        return 0

    if args.command == "contacts":
        text = read(args.file)
        found = outreach.find_contacts(text, args.company, source=f"read from {Path(args.file).name}")
        if not found:
            print("no addresses found")
            return 1
        if args.save:
            db.init(store)
            with db.connect(store) as connection:
                for contact in found:
                    db.record_contact(
                        connection,
                        email=contact.email,
                        company=contact.company,
                        role=contact.role,
                        source=contact.source,
                        verified=contact.verified,
                    )
        for contact in found:
            print(f"{contact.email:<40} {contact.role:<10} {'verified' if contact.verified else 'unverified'}")
        return 0

    if args.command == "draft":
        contact = outreach.Contact(
            email=args.to, company=args.company, role="person", source="draft", verified=True
        )
        letter = outreach.draft(contact, args.company, args.role, args.reason)
        print(f"To: {contact.email}\nSubject: {letter.subject}\n")
        print(letter.body + profile(config).signature)
        return 0

    if args.command == "send":
        contact = outreach.Contact(
            email=args.to,
            company=args.company,
            role="person",
            source="read off a page" if args.verified else "unverified",
            verified=args.verified,
        )
        letter = outreach.draft(contact, args.company, args.role, args.reason)
        smtp = settings(config)
        db.init(store)
        with db.connect(store) as connection:
            outcome = outreach.send(
                connection,
                smtp,
                profile(config),
                contact,
                letter,
                source_note=args.source_note,
                dry_run=not args.send,
                run_count=db.sent_today(connection),
            )
        print(outcome)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
