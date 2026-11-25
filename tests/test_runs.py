"""Runs started from the dashboard: the thread, the status, and the ledger.

The point of these tests is the seam the CLI never needed: a browser run whose
progress a web page can read while it is still going. The browser itself is
stubbed — one fixture test in ``test_recorded_forms.py`` already proves the
filler, and re-proving it through Flask would only make the suite slower.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from jobhunter import db, runs, web

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def clean_registry():
    """Runs live in a module-level registry; a leftover would be seen by the
    next test's job page, because every test's store starts at job id 1."""
    runs._RUNS.clear()
    yield
    runs._RUNS.clear()


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "jobs.db"
    db.init(path)
    return path


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[profile]\nname = "Jed Dahlke"\ncv = ""\n\n[search]\nterms = ["risk"]\n',
        encoding="utf-8",
    )
    return path


def add_job(store: Path, *, url: str = "https://apply.example.test/j/1", title: str = "Risk Analyst") -> int:
    with db.connect(store) as connection:
        return db.upsert_job(
            connection,
            url=url,
            company="Acme",
            title=title,
            decision="apply",
            description="A risk role.",
        )


def fake_report(**overrides):
    report = runs_fill_report()
    for key, value in overrides.items():
        setattr(report, key, value)
    return report


def runs_fill_report():
    from jobhunter import fill

    return fill.FillReport(url="https://apply.example.test/j/1", ats="workable")


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_record_attempt_creates_a_draft_row_with_the_evidence(tmp_path, store):
    job_id = add_job(store)
    report = fake_report(
        filled=["first_name", "email"],
        required_left=["Degree Status"],
        screenshot=str(tmp_path / "shot.png"),
        trace_path=str(tmp_path / "trace"),
    )

    runs.record_attempt(store, job_id, report, variant="Risk", market="uk")

    with db.connect(store) as connection:
        row = connection.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    assert row["stage"] == "draft"
    assert row["variant"] == "Risk"
    assert "filled form (workable)" in row["note"]
    assert "still required: Degree Status" in row["note"]
    assert "submitted=False" in row["note"]


def test_record_attempt_appends_instead_of_duplicating(store):
    job_id = add_job(store)
    runs.record_attempt(store, job_id, fake_report(filled=["email"]))
    runs.record_attempt(store, job_id, fake_report(filled=["email"]))

    with db.connect(store) as connection:
        rows = connection.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["note"].count("filled form") == 2


def test_a_run_reports_progress_as_json(store, config_path):
    job_id = add_job(store)
    run = runs.Run(id="run-test-json", job_id=job_id, status="running", title="Risk Analyst")
    run.filled = ["email"]
    run.review = [("Salary?", "no answer in the bank")]
    runs._RUNS[run.id] = run

    client = web.create_app(store, config_path).test_client()
    response = client.get("/runs/run-test-json.json")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "running"
    assert payload["filled"] == ["email"]
    assert payload["review"] == [["Salary?", "no answer in the bank"]]


def test_the_done_button_releases_a_held_browser(store, config_path):
    job_id = add_job(store)
    run = runs.Run(id="run-test-hold", job_id=job_id, status="waiting")
    runs._RUNS[run.id] = run

    client = web.create_app(store, config_path).test_client()
    response = client.post("/runs/run-test-hold/release")
    assert response.status_code == 200
    assert run.release.is_set()


def test_starting_a_fill_does_not_block_the_request(store, config_path, monkeypatch):
    """The request returns while the worker is still going; that is the seam."""
    job_id = add_job(store)
    gate = threading.Event()
    seen: dict = {}

    def worker(run, *args, **kwargs):
        seen["run"] = run
        gate.wait(5)
        run.status = "done"

    monkeypatch.setattr(runs, "_fill_worker", worker)
    client = web.create_app(store, config_path).test_client()

    response = client.post(f"/jobs/{job_id}/fill", data={"hold": "1"})

    assert response.status_code == 302
    assert "/jobs/" in response.headers["Location"]
    assert wait_until(lambda: "run" in seen)
    assert runs.active_for(job_id) is not None
    gate.set()
    assert wait_until(lambda: runs.active_for(job_id) is None)


def test_a_second_fill_for_the_same_job_reuses_the_running_one(store, monkeypatch):
    job_id = add_job(store)
    gate = threading.Event()

    def worker(run, *args, **kwargs):
        gate.wait(5)

    monkeypatch.setattr(runs, "_fill_worker", worker)
    first = runs.start_fill(store, {}, job_id, root=ROOT)
    second = runs.start_fill(store, {}, job_id, root=ROOT)
    gate.set()
    assert first.id == second.id


def test_the_job_page_offers_the_fill_controls(store, config_path):
    job_id = add_job(store)
    client = web.create_app(store, config_path).test_client()

    body = client.get(f"/jobs/{job_id}").get_data(as_text=True)
    assert "Fill the form" in body
    assert 'name="hold"' in body
    assert 'name="submit"' in body
    assert 'id="run-status"' in body


def test_discovery_runs_in_the_background_and_keeps_its_log(store, tmp_path, monkeypatch):
    from jobhunter import discover

    firms = tmp_path / "firms.toml"
    firms.write_text(
        '[[firms]]\nname = "Acme"\nats = "greenhouse"\nslug = "acme"\n', encoding="utf-8"
    )

    def fake_run(config, registry, *, on_progress=None, **kwargs):
        on_progress("  Acme: 5 listed, 2 match title, 2 in scope")
        result = discover.Result(firm="Acme")
        result.seen = 5
        result.after_title = 2
        result.kept = 2
        result.stored = 2
        return [result]

    monkeypatch.setattr(discover, "run", fake_run)
    run = runs.start_discovery(store, {}, firms)

    assert wait_until(lambda: run.status == "done")
    assert any("Acme: 5 listed" in line for line in run.lines)
    assert run.message.startswith("2 new postings")


def test_a_second_discovery_reuses_the_running_one(store, tmp_path, monkeypatch):
    firms = tmp_path / "firms.toml"
    firms.write_text('[[firms]]\nname = "Acme"\nats = "greenhouse"\nslug = "acme"\n', encoding="utf-8")
    gate = threading.Event()

    def worker(run, *args, **kwargs):
        gate.wait(5)

    monkeypatch.setattr(runs, "_discovery_worker", worker)
    first = runs.start_discovery(store, {}, firms)
    second = runs.start_discovery(store, {}, firms)
    gate.set()
    assert first.id == second.id


def test_the_jobs_page_has_a_read_the_boards_button(store, config_path):
    client = web.create_app(store, config_path).test_client()
    body = client.get("/jobs").get_data(as_text=True)
    assert "Read the boards now" in body
    assert 'action="/discover"' in body


def test_pressing_read_the_boards_starts_a_run(store, config_path, monkeypatch):
    gate = threading.Event()

    def worker(run, *args, **kwargs):
        gate.wait(5)

    monkeypatch.setattr(runs, "_discovery_worker", worker)
    client = web.create_app(store, config_path).test_client()
    response = client.post("/discover", data={})
    gate.set()

    assert response.status_code == 302
    assert "run=" in response.headers["Location"]
