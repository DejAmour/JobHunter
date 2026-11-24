"""Browser runs the dashboard can start and watch.

The CLI blocks a terminal for the length of a form. The dashboard cannot: a
request must return while the browser is still working, so a run is a thread
plus a small record of what it is doing, which the job page polls.

The one interactive moment - a headed browser left open so a human can finish
the parts this tool deliberately will not touch, and submit - is a
``threading.Event`` instead of ``input()``. The terminal version still works; it
just passes its own waiter.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import db

#: How long a held browser waits for the human before closing itself. Long
#: enough to read a form, short enough that a forgotten tab does not live
#: forever.
HOLD_SECONDS = 30 * 60


@dataclass
class Run:
    id: str
    job_id: int
    kind: str = "fill"
    status: str = "queued"  # queued | running | waiting | done | failed
    message: str = ""
    title: str = ""
    company: str = ""
    variant: str = ""
    market: str = ""
    steps: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)
    empty_answers: list[str] = field(default_factory=list)
    answered: list[tuple[str, str]] = field(default_factory=list)
    review: list[tuple[str, str]] = field(default_factory=list)
    required_left: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    screenshot: str = ""
    trace: str = ""
    submitted: bool = False
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None
    release: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running", "waiting")

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "title": self.title,
            "company": self.company,
            "variant": self.variant,
            "market": self.market,
            "steps": self.steps,
            "filled": self.filled,
            "empty_answers": self.empty_answers,
            "answered": [list(pair) for pair in self.answered],
            "review": [list(pair) for pair in self.review],
            "required_left": self.required_left,
            "notes": self.notes,
            "has_screenshot": bool(self.screenshot),
            "trace": self.trace,
            "submitted": self.submitted,
            "error": self.error,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
        }


_RUNS: dict[str, Run] = {}
_LOCK = threading.Lock()


def get(run_id: str) -> Run | None:
    with _LOCK:
        return _RUNS.get(run_id)


def active_for(job_id: int) -> Run | None:
    with _LOCK:
        for run in _RUNS.values():
            if run.job_id == job_id and run.active:
                return run
    return None


def recent(limit: int = 20) -> list[Run]:
    with _LOCK:
        return sorted(_RUNS.values(), key=lambda run: run.started, reverse=True)[:limit]


def start_fill(
    store: Path,
    config: dict,
    job_id: int,
    *,
    root: Path,
    advance: bool = False,
    submit: bool = False,
    hold: bool = True,
    ats: str = "",
) -> Run:
    """Prepare the pack, then fill the form in a background thread.

    Preparing first is deliberate: the pack is what a human reads while the
    browser is open, and it records a draft even if the form itself fails.
    """
    with _LOCK:
        running = next(
            (run for run in _RUNS.values() if run.job_id == job_id and run.active), None
        )
        if running is not None:
            return running
        run = Run(id=uuid.uuid4().hex[:10], job_id=job_id)
        _RUNS[run.id] = run

    thread = threading.Thread(
        target=_fill_worker,
        args=(run, store, config, root),
        kwargs={"advance": advance, "submit": submit, "hold": hold, "ats": ats},
        name=f"fill-{job_id}",
        daemon=True,
    )
    thread.start()
    return run


def _fill_worker(run: Run, store: Path, config: dict, root: Path, **options) -> None:
    from . import accounts as accounts_module
    from . import apply as apply_pack
    from . import cv
    from . import fill as form_fill
    from . import mailbox

    try:
        run.status = "running"
        run.message = "preparing the pack"

        cv_path = config.get("profile", {}).get("cv", "")
        cv_text = ""
        if cv_path and Path(cv_path).is_file():
            if Path(cv_path).suffix.lower() in {".pdf", ".docx"}:
                cv_text = cv.extract(Path(cv_path))[0]
            else:
                cv_text = Path(cv_path).read_text(encoding="utf-8", errors="ignore")
        apply_pack.prepare(store, root, run.job_id, config, cv_text=cv_text, record_it=True)

        with db.connect(store) as connection:
            pack = apply_pack.build(connection, run.job_id, config, root=root, cv_text=cv_text)
        run.title = pack.job["title"]
        run.company = pack.job["company"]
        run.variant = pack.variant
        run.market = pack.market

        cv_file = root / "output" / pack.cv_path
        values = form_fill.values_from(
            config, cv_path=str(cv_file), org=pack.job["company"], market=pack.market
        )
        url = pack.job["url"]
        run.message = f"opening {url}"

        home = store.parent
        account = accounts_module.for_url(home, url)
        create_account = bool(account) and not accounts_module.session_path(home, account).is_file()

        on_code = None
        if mailbox.available(config):
            started = time.time()

            def fetch_code():
                found = mailbox.latest_code(config, since=started, wait_seconds=120)
                return found.value if found else ""

            on_code = fetch_code

        waiter = None
        if options.get("hold"):
            waiter = lambda: _hold(run)  # noqa: E731 - a thunk, nothing more

        report = form_fill.run(
            url,
            values,
            ats=options.get("ats", ""),
            ctx=form_fill.context_for(config, pack),
            submit=bool(options.get("submit")),
            advance=bool(options.get("advance")),
            headless=not options.get("hold"),
            hold=waiter,
            screenshot_dir=root / "output" / "applications" / apply_pack._slug(
                f"{pack.job['company']}-{pack.job['title']}"
            ),
            account=account,
            home=home,
            create_account=create_account,
            on_code=on_code,
            mail_config=config,
        )

        run.steps = list(report.steps)
        run.filled = list(report.filled)
        run.empty_answers = list(report.empty_answers)
        run.answered = list(report.questions_answered)
        run.review = list(report.questions_review)
        run.required_left = list(report.required_left)
        run.notes = list(report.notes)
        run.screenshot = report.screenshot
        run.trace = report.trace_path
        run.submitted = report.submitted
        run.error = report.error
        record_attempt(store, run.job_id, report, variant=pack.variant, market=pack.market)

        if report.error:
            run.status = "failed"
            run.message = report.error
        else:
            run.status = "done"
            run.message = "submitted" if report.submitted else "filled; nothing sent"
    except Exception as error:  # noqa: BLE001 - a run may not take the server down
        run.status = "failed"
        run.error = f"{type(error).__name__}: {error}"
        run.message = run.error
    finally:
        run.finished = time.time()


def _hold(run: Run) -> None:
    """Keep the browser open until the dashboard says the human is finished."""
    run.status = "waiting"
    run.message = (
        "browser open: finish and submit the application there, then press Done here"
    )
    run.release.wait(HOLD_SECONDS)
    run.status = "running"
    run.message = "closing the browser"


def record_attempt(store: Path, job_id: int, report, *, variant: str = "", market: str = "") -> None:
    """Write what the run did into the application ledger.

    One row per application attempt: created if the pack did not already record
    one, appended to if it did. ``submitted`` stays False unless the page moved.
    """
    with db.connect(store) as connection:
        row = connection.execute(
            "SELECT id, note FROM applications WHERE job_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        stamp = db.now()[:16].replace("T", " ")
        entry = f"{stamp}: filled form ({report.ats}), submitted={bool(report.submitted)}"
        if report.steps:
            entry += f", steps {' -> '.join(report.steps)}"
        if report.screenshot:
            entry += f", screenshot {report.screenshot}"
        if report.trace_path:
            entry += f", trace {report.trace_path}"
        if report.required_left:
            entry += f", still required: {', '.join(report.required_left)[:300]}"

        sent = bool(report.submitted)
        reached = bool(report.filled or report.steps)
        if row is None and reached:
            db.record_application(
                connection,
                job_id=job_id,
                status="draft",
                approval="pending",
                proof_path=report.screenshot or report.trace_path or None,
                note=entry,
                stage="applied" if sent else "draft",
                variant=variant,
                country=market,
            )
        elif row is not None:
            note = (row["note"] or "").strip()
            connection.execute(
                "UPDATE applications SET note = ?, updated_at = ? WHERE id = ?",
                (f"{note}\n{entry}".strip(), db.now(), row["id"]),
            )
            if sent:
                db.set_application_stage(connection, int(row["id"]), "applied")
