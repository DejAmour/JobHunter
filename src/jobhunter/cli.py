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

    serve_parser = commands.add_parser("serve", help="run the local dashboard")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--host", default="127.0.0.1")

    import_parser = commands.add_parser("import", help="read a page you saved yourself (LinkedIn, Indeed)")
    import_parser.add_argument("--file", required=True)
    import_parser.add_argument("--kind", default="auto", choices=("auto", "linkedin", "indeed", "jsonld"))
    import_parser.add_argument("--dry-run", action="store_true")

    login_parser = commands.add_parser("login", help="capture a signed-in browser session")
    login_parser.add_argument("--site", default="indeed", choices=("indeed", "linkedin"))

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
    fill_parser.add_argument("--ats", default="", help="force a board type (greenhouse, lever) instead of detecting it")

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
        report = form_fill.run(
            url,
            values,
            ats=args.ats,
            ctx=form_fill.context_for(config, pack),
            submit=args.submit,
            headless=args.headless,
            screenshot_dir=root / "output" / "applications" / apply_pack._slug(
                f"{pack.job['company']}-{pack.job['title']}"
            ),
        )
        print()
        print(report.summary())

        # Record the attempt against the application, so the ledger says a form
        # was filled even when nothing was sent.
        with db.connect(store) as connection:
            row = connection.execute(
                "SELECT id, note FROM applications WHERE job_id = ? ORDER BY id DESC LIMIT 1",
                (args.job,),
            ).fetchone()
            if row is not None:
                stamp = db.now()[:16].replace("T", " ")
                note = (row["note"] or "").strip()
                entry = f"{stamp}: filled form ({report.ats}), submitted={bool(args.submit)}"
                if report.screenshot:
                    entry += f", screenshot {report.screenshot}"
                connection.execute(
                    "UPDATE applications SET note = ?, updated_at = ? WHERE id = ?",
                    (f"{note}\n{entry}".strip(), db.now(), row["id"]),
                )
                if args.submit and report.submitted:
                    db.set_application_stage(connection, int(row["id"]), "applied")
        return 0 if not report.error else 1

    if args.command == "login":
        from . import sessions

        return sessions.login(args.site, home())

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
