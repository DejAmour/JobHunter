"""The local dashboard.

One process, one SQLite file, bound to 127.0.0.1. Nothing leaves the machine and
there is no login because there is no remote access; if that ever changes, the
bound address changes first.

Deliberately server-rendered with no build step: the point is that in six months
``python -m jobhunter serve`` still works without a node_modules.
"""

from __future__ import annotations

import sqlite3
import tomllib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for

from . import cv, db, screening

_CV_CACHE: dict[tuple, str] = {}


def _candidate(config: dict) -> screening.Candidate:
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


def _cv_text_cached(path: Path) -> str:
    """Extract the CV once per file version; pypdf on every request is wasteful."""
    key = (str(path), path.stat().st_mtime)
    return _CV_CACHE.get(key) if key in _CV_CACHE else _CV_CACHE.setdefault(key, cv.extract(path)[0])

STAGES = ("draft", "applied", "assessment", "interview", "final", "offer", "rejected", "withdrawn")
DECISIONS = ("apply", "ask", "exclude")

# Agencies dominate aggregator results. They are not disqualifying — plenty of UK
# investment hiring runs through them — but they should be visible as such, so a
# "direct employer only" view is one click away. Matched on words that agencies
# put in their own names, plus a list of the ones that keep appearing. "Partners"
# is deliberately absent: Partners Group and Puma Growth Partners are employers.
AGENCY_WORDS = (
    "recruitment", "recruiter", "staffing", "resourcing", "talent",
    "executive search", "search & selection", "search and selection", "consultancy",
)

AGENCY_NAMES = (
    "harnham", "alexander lloyd", "robert walters", "michael page", "page personnel",
    "hays", "robert half", "morgan consulting", "kaizen recruitment", "profusion",
    "fourquarters", "bluefin", "perigon group", "salt search", "cer financial",
    "c james", "tebbutt", "the aquarium", "dash recruitment", "solitaire executive",
    "william buck", "hudson", "lorien", "la fosse", "eames", "durlston",
)


def is_agency(company: str, extra: tuple[str, ...] = ()) -> bool:
    name = f" {company.lower().strip()} "
    if any(word in name for word in (*AGENCY_WORDS, *[e.lower() for e in extra])):
        return True
    return any(known in name for known in AGENCY_NAMES)


@contextmanager
def _connect(store: Path):
    """A connection that is always closed.

    ``with sqlite3.connect(...)`` looks like it closes and does not â€” it commits
    and leaves the handle open, so every request leaked one. Enough leaked read
    transactions and the scraper could no longer write.
    """
    connection = sqlite3.connect(store, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _load_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def create_app(store: Path, config_path: Path) -> Flask:
    app = Flask(__name__)
    db.init(store)
    config = _load_config(config_path)
    cvs = list(config.get("search", {}).get("cv_variants", []) or [])

    @app.context_processor
    def inject() -> dict:
        with _connect(store) as connection:
            ready = connection.execute(
                "SELECT COUNT(1) n FROM jobs WHERE decision = 'apply'"
            ).fetchone()["n"]
            open_apps = connection.execute(
                "SELECT COUNT(1) n FROM applications "
                "WHERE stage NOT IN ('rejected','withdrawn')"
            ).fetchone()["n"]
            total = connection.execute("SELECT COUNT(1) n FROM jobs").fetchone()["n"]
            last = connection.execute("SELECT MAX(discovered_at) t FROM jobs").fetchone()["t"]
        return {
            "stages": STAGES,
            "decisions": DECISIONS,
            "today": datetime.now(UTC).date().isoformat(),
            "ready_count": ready,
            "open_count": open_apps,
            "total_count": total,
            "last_run": last,
        }

    # --- overview ---------------------------------------------------------

    @app.get("/")
    def overview():
        with _connect(store) as connection:
            counts = {
                row["decision"]: row["n"]
                for row in connection.execute(
                    "SELECT decision, COUNT(*) n FROM jobs GROUP BY decision"
                )
            }
            by_country = {
                row["country"]: row["n"]
                for row in connection.execute(
                    "SELECT country, COUNT(*) n FROM jobs GROUP BY country"
                )
            }
            stages = {
                row["stage"]: row["n"]
                for row in connection.execute(
                    "SELECT stage, COUNT(*) n FROM applications GROUP BY stage"
                )
            }
            upcoming_assessments = connection.execute(
                """
                SELECT a.*, ap.id AS app_id, j.company, j.title
                FROM assessments a
                JOIN applications ap ON ap.id = a.application_id
                JOIN jobs j ON j.id = ap.job_id
                WHERE a.status = 'pending'
                ORDER BY COALESCE(a.due_at, '9999') LIMIT 8
                """
            ).fetchall()
            upcoming_interviews = connection.execute(
                """
                SELECT i.*, ap.id AS app_id, j.company, j.title
                FROM interviews i
                JOIN applications ap ON ap.id = i.application_id
                JOIN jobs j ON j.id = ap.job_id
                WHERE i.status = 'scheduled'
                ORDER BY COALESCE(i.scheduled_at, '9999') LIMIT 8
                """
            ).fetchall()
            recent = connection.execute(
                "SELECT * FROM jobs ORDER BY discovered_at DESC, id DESC LIMIT 12"
            ).fetchall()
            best = connection.execute(
                """
                SELECT * FROM jobs
                ORDER BY CASE decision WHEN 'apply' THEN 0 WHEN 'ask' THEN 1 ELSE 2 END,
                         COALESCE(coverage, -1) DESC,
                         discovered_at DESC
                LIMIT 8
                """
            ).fetchall()
            last_run = connection.execute("SELECT MAX(discovered_at) t FROM jobs").fetchone()["t"]
            outreach = connection.execute(
                """
                SELECT COUNT(*) n,
                       SUM(CASE WHEN dry_run = 0 THEN 1 ELSE 0 END) sent
                FROM outreach
                """
            ).fetchone()
            cvs_count = connection.execute("SELECT COUNT(*) n FROM cv_versions").fetchone()["n"]

        return render_template(
            "overview.html",
            counts=counts,
            by_country=by_country,
            stages=stages,
            assessments=upcoming_assessments,
            interviews=upcoming_interviews,
            recent=recent,
            best=best,
            outreach=outreach,
            cvs_count=cvs_count,
            last_run=last_run,
        )

    # --- jobs -------------------------------------------------------------

    @app.get("/jobs")
    def jobs():
        decision = request.args.get("decision", "")
        country = request.args.get("country", "")
        query = request.args.get("q", "").strip()
        senior = request.args.get("senior", "")
        agency = request.args.get("agency", "")
        page = max(1, int(request.args.get("page", 1) or 1))
        per_page = 75

        where = " WHERE 1 = 1"
        params: list = []
        if decision:
            where += " AND decision = ?"
            params.append(decision)
        if country:
            where += " AND country = ?"
            params.append(country)
        if query:
            where += " AND (title LIKE ? OR company LIKE ?)"
            params += [f"%{query}%", f"%{query}%"]
        if senior == "hide":
            where += " AND seniority = ''"
        if agency == "hide":
            # Filtered in Python because the agency rule is a word list, not a column.
            pass

        order = (
            " ORDER BY CASE decision WHEN 'apply' THEN 0 WHEN 'ask' THEN 1 ELSE 2 END,"
            " coverage DESC NULLS LAST, company, title"
        )

        with _connect(store) as connection:
            total = connection.execute(f"SELECT COUNT(1) n FROM jobs{where}", params).fetchone()["n"]
            rows = connection.execute(
                f"SELECT * FROM jobs{where}{order} LIMIT ? OFFSET ?",
                [*params, per_page, (page - 1) * per_page],
            ).fetchall()
            applied = {row["job_id"] for row in connection.execute("SELECT job_id FROM applications")}

        if agency == "hide":
            rows = [row for row in rows if not is_agency(row["company"])]

        pages = max(1, (total + per_page - 1) // per_page)
        return render_template(
            "jobs.html", jobs=rows, decision=decision, country=country, query=query,
            senior=senior, agency=agency, applied=applied, page=page, pages=pages, total=total,
            first=(page - 1) * per_page + 1, last=min(page * per_page, total),
            is_agency=is_agency,
        )

    @app.get("/jobs/<int:job_id>")
    def job(job_id: int):
        with _connect(store) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                abort(404)
            application = connection.execute(
                "SELECT * FROM applications WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)
            ).fetchone()
            variants = connection.execute(
                "SELECT * FROM cv_versions ORDER BY label, market"
            ).fetchall()

        # Re-run the screen so the page can show its working, not just a verdict.
        report = None
        cv_path = Path(config.get("profile", {}).get("cv", ""))
        if row["description"] and cv_path.is_file():
            try:
                report = screening.screen(
                    row["description"], _cv_text_cached(cv_path), _candidate(config)
                )
            except Exception:
                report = None

        missing = [item.lstrip("• ").strip() for item in (report.missing if report else ())]

        return render_template(
            "job.html",
            job=row,
            application=application,
            variants=variants,
            variants_config=cvs,
            report=report,
            missing=missing,
            agency=is_agency(row["company"]),
        )

    @app.post("/jobs/<int:job_id>/decision")
    def set_decision(job_id: int):
        decision = request.form.get("decision", "")
        if decision not in DECISIONS:
            abort(400)
        with _connect(store) as connection:
            connection.execute("UPDATE jobs SET decision = ? WHERE id = ?", (decision, job_id))
        return redirect(request.form.get("next") or url_for("job", job_id=job_id))

    @app.post("/jobs/<int:job_id>/note")
    def set_note(job_id: int):
        note = request.form.get("notes", "")[:2000]
        with _connect(store) as connection:
            connection.execute("UPDATE jobs SET notes = ? WHERE id = ?", (note, job_id))
        return redirect(url_for("job", job_id=job_id))

    # --- applications -----------------------------------------------------

    @app.post("/jobs/<int:job_id>/apply")
    def create_application(job_id: int):
        variant = request.form.get("variant", "")
        country = request.form.get("country", "")
        with _connect(store) as connection:
            existing = connection.execute(
                "SELECT id FROM applications WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)
            ).fetchone()
            if existing:
                return redirect(url_for("application", application_id=existing["id"]))
            db.record_application(
                connection,
                job_id=job_id,
                status="draft",
                approval="pending",
                stage="draft",
                variant=variant,
                country=country,
                note=request.form.get("note", ""),
            )
            connection.commit()
        return redirect(url_for("applications"))

    @app.get("/applications")
    def applications():
        stage = request.args.get("stage", "")
        sql = """
            SELECT ap.*, j.company, j.title, j.location, j.url, j.country AS job_country, j.decision
            FROM applications ap JOIN jobs j ON j.id = ap.job_id
        """
        params: list = []
        if stage:
            sql += " WHERE ap.stage = ?"
            params.append(stage)
        sql += " ORDER BY ap.updated_at DESC, ap.id DESC"
        with _connect(store) as connection:
            rows = connection.execute(sql, params).fetchall()
        return render_template("applications.html", applications=rows, stage=stage)

    @app.get("/applications/<int:application_id>")
    def application(application_id: int):
        with _connect(store) as connection:
            row = connection.execute(
                """
                SELECT ap.*, j.company, j.title, j.location, j.url, j.decision, j.coverage,
                       j.country AS job_country, j.description
                FROM applications ap JOIN jobs j ON j.id = ap.job_id
                WHERE ap.id = ?
                """,
                (application_id,),
            ).fetchone()
            if row is None:
                abort(404)
            assessments = connection.execute(
                "SELECT * FROM assessments WHERE application_id = ? ORDER BY COALESCE(due_at, '9999')",
                (application_id,),
            ).fetchall()
            interviews = connection.execute(
                "SELECT * FROM interviews WHERE application_id = ? ORDER BY COALESCE(scheduled_at, '9999')",
                (application_id,),
            ).fetchall()
            messages = connection.execute(
                "SELECT o.*, c.email, c.name FROM outreach o LEFT JOIN contacts c ON c.id = o.contact_id "
                "WHERE o.job_id = ? ORDER BY o.sent_at DESC",
                (row["job_id"],),
            ).fetchall()
            variants = connection.execute("SELECT * FROM cv_versions ORDER BY label, market").fetchall()
        return render_template(
            "application.html",
            application=row,
            assessments=assessments,
            interviews=interviews,
            messages=messages,
            variants=variants,
        )

    @app.post("/applications/<int:application_id>/stage")
    def set_stage(application_id: int):
        stage = request.form.get("stage", "")
        if stage not in STAGES:
            abort(400)
        with _connect(store) as connection:
            db.set_application_stage(connection, application_id, stage)
        return redirect(url_for("application", application_id=application_id))

    @app.post("/applications/<int:application_id>/details")
    def set_details(application_id: int):
        with _connect(store) as connection:
            connection.execute(
                "UPDATE applications SET variant = ?, deadline = ?, note = ?, updated_at = ? WHERE id = ?",
                (
                    request.form.get("variant", ""),
                    request.form.get("deadline") or None,
                    request.form.get("note", "")[:4000],
                    db.now(),
                    application_id,
                ),
            )
        return redirect(url_for("application", application_id=application_id))

    @app.post("/applications/<int:application_id>/assessment")
    def add_assessment(application_id: int):
        with _connect(store) as connection:
            db.add_assessment(
                connection,
                application_id=application_id,
                kind=request.form.get("kind", "online"),
                due_at=request.form.get("due_at") or None,
                note=request.form.get("note", ""),
            )
            connection.execute(
                "UPDATE applications SET stage = 'assessment', updated_at = ? WHERE id = ? AND stage IN ('draft','applied')",
                (db.now(), application_id),
            )
        return redirect(url_for("application", application_id=application_id))

    @app.post("/applications/<int:application_id>/interview")
    def add_interview(application_id: int):
        with _connect(store) as connection:
            db.add_interview(
                connection,
                application_id=application_id,
                round=request.form.get("round", "first"),
                scheduled_at=request.form.get("scheduled_at") or None,
                mode=request.form.get("mode", ""),
                note=request.form.get("note", ""),
            )
            connection.execute(
                "UPDATE applications SET stage = 'interview', updated_at = ? WHERE id = ? AND stage IN ('draft','applied','assessment')",
                (db.now(), application_id),
            )
        return redirect(url_for("application", application_id=application_id))

    @app.post("/assessments/<int:assessment_id>/status")
    def assessment_status(assessment_id: int):
        status = request.form.get("status", "pending")
        with _connect(store) as connection:
            connection.execute("UPDATE assessments SET status = ? WHERE id = ?", (status, assessment_id))
            row = connection.execute(
                "SELECT application_id FROM assessments WHERE id = ?", (assessment_id,)
            ).fetchone()
        return redirect(url_for("application", application_id=row["application_id"]))

    @app.post("/interviews/<int:interview_id>/status")
    def interview_status(interview_id: int):
        status = request.form.get("status", "scheduled")
        with _connect(store) as connection:
            connection.execute("UPDATE interviews SET status = ? WHERE id = ?", (status, interview_id))
            row = connection.execute(
                "SELECT application_id FROM interviews WHERE id = ?", (interview_id,)
            ).fetchone()
        return redirect(url_for("application", application_id=row["application_id"]))

    # --- outreach ---------------------------------------------------------

    @app.get("/outreach")
    def outreach():
        with _connect(store) as connection:
            contacts = connection.execute(
                "SELECT * FROM contacts ORDER BY verified DESC, company, email"
            ).fetchall()
            messages = connection.execute(
                """
                SELECT o.*, c.email, c.name, c.company, j.title AS job_title
                FROM outreach o
                LEFT JOIN contacts c ON c.id = o.contact_id
                LEFT JOIN jobs j ON j.id = o.job_id
                ORDER BY o.sent_at DESC LIMIT 100
                """
            ).fetchall()
            per_domain = connection.execute(
                """
                SELECT lower(substr(c.email, instr(c.email, '@') + 1)) AS domain, COUNT(*) n
                FROM outreach o JOIN contacts c ON c.id = o.contact_id
                WHERE o.dry_run = 0 AND substr(o.sent_at, 1, 10) = substr(?, 1, 10)
                GROUP BY domain ORDER BY n DESC
                """,
                (db.now(),),
            ).fetchall()
        return render_template(
            "outreach.html", contacts=contacts, messages=messages, per_domain=per_domain
        )

    # --- CV library -------------------------------------------------------

    @app.get("/cvs")
    def cvs_page():
        with _connect(store) as connection:
            rows = connection.execute(
                "SELECT * FROM cv_versions ORDER BY label, market"
            ).fetchall()
        with _connect(store) as connection:
            used = {
                row["variant"]: row["n"]
                for row in connection.execute(
                    "SELECT variant, COUNT(*) n FROM applications WHERE variant <> '' GROUP BY variant"
                )
            }
        return render_template("cvs.html", cvs=rows, used=used)

    @app.post("/cvs/scan")
    def scan_cvs():
        """Re-read the built PDFs so page count and parse score are not stale."""
        out_dir = Path(config.get("profile", {}).get("cv", "")).parent
        terms = tuple(config.get("search", {}).get("terms", []))
        with _connect(store) as connection:
            for pdf in sorted(out_dir.glob("*.pdf")):
                result = cv.audit(pdf, terms)
                stem = pdf.stem  # "Jed Dahlke CV Risk (AU)"
                market = "au" if stem.endswith("(AU)") else "uk"
                label = stem.replace("(AU)", "").replace("Jed Dahlke CV", "").strip() or "Asset Management"
                pages = result.pages or 0
                db.record_cv(
                    connection,
                    label=label,
                    market=market,
                    path=str(pdf),
                    pages=pages,
                    parse_score=result.confidence,
                )
            connection.commit()
        return redirect(url_for("cvs_page"))

    return app


def serve(store: Path, config_path: Path, host: str = "127.0.0.1", port: int = 8765) -> int:
    app = create_app(store, config_path)
    print(f"dashboard: http://{host}:{port}  (ctr-c to stop)")
    app.run(host=host, port=port, debug=False, use_reloader=False)
    return 0

