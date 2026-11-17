"""The trace has to answer "what did the page actually do?" — and keep secrets out."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from jobhunter import evidence

playwright = pytest.importorskip("playwright.sync_api")


# --- redaction (fast, no browser) ---------------------------------------


def test_a_password_never_reaches_the_trace():
    body = json.dumps({"email": "a@b.com", "password": "hunter2!", "verifyPassword": "hunter2!"})
    out = evidence.redact_body(body, secrets=("hunter2!",))
    assert "hunter2!" not in out
    assert out.count(evidence.MASK) >= 2
    assert "a@b.com" in out


def test_a_password_in_a_form_body_is_masked_even_unlisted():
    out = evidence.redact_body("email=a%40b.com&password=s3cret&postcode=EX5", secrets=())
    assert "s3cret" not in out
    assert "email=a%40b.com" in out
    assert "postcode=EX5" in out


def test_a_token_in_a_query_string_is_masked():
    out = evidence.redact_url("https://x.test/cb?token=abc123&job=R1", secrets=())
    assert "abc123" not in out
    assert "job=R1" in out


def test_plain_text_redaction_catches_an_unlisted_secret_value():
    out = evidence.redact_text("failed with password hunter2 in it", secrets=("hunter2",))
    assert "hunter2" not in out


def test_a_short_secret_is_not_replaced_everywhere():
    """Replacing a two-character secret would shred every word in the log."""
    out = evidence.redact_text("a cat sat", secrets=("a",))
    assert out == "a cat sat"


# --- the recorder, against a real local server --------------------------


REGISTER = """<!doctype html><html><body>
  <form method="post" action="/register">
    <input name="email" id="email" type="text">
    <input name="password" id="password" type="password">
    <button type="submit" id="go">Create Account</button>
  </form>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self._send(REGISTER)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._send("<html><body>Check your email</body></html>")

    def _send(self, body: str) -> None:
        payload = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence the test output
        pass


@pytest.fixture()
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def page():
    runtime = playwright.sync_playwright().start()
    browser = runtime.chromium.launch(headless=True)
    tab = browser.new_page()
    try:
        yield tab
    finally:
        browser.close()
        runtime.stop()


def test_a_silent_form_is_diagnosable_from_the_trace(tmp_path, site, page):
    """The M&G failure: did anything get posted, and did the server answer?"""
    trace = evidence.Evidence(tmp_path / "trace", secrets=("hunter2!",), label="probe")
    trace.attach(page.context, page)

    page.goto(site, wait_until="domcontentloaded")
    page.fill("#email", "candidate@example.com")
    page.fill("#password", "hunter2!")
    page.click("#go")
    page.wait_for_timeout(600)

    folder = trace.save(page, outcome="still on the account form")

    assert (folder / "summary.txt").is_file()
    assert (folder / "page.html").is_file()
    assert (folder / "screenshot.png").is_file()
    assert (folder / "network.jsonl").is_file()
    assert (folder / "meta.json").is_file()

    posts = trace.posts()
    assert [row["url"] for row in posts] == [f"{site}/register"]
    # The decisive bit: the summary names the submission and its answer.
    summary = (folder / "summary.txt").read_text(encoding="utf-8")
    assert "POST /register -> 200" in summary
    # And no password anywhere on disk.
    for name in ("summary.txt", "network.jsonl", "page.html", "meta.json", "console.json"):
        text = (folder / name).read_text(encoding="utf-8")
        assert "hunter2!" not in text, name


def test_a_run_with_no_submission_says_so(tmp_path, site, page):
    """If the click never reached a handler, zero submissions is the answer."""
    trace = evidence.Evidence(tmp_path / "trace", label="dead")
    trace.attach(page.context, page)
    page.goto(site, wait_until="domcontentloaded")
    page.fill("#email", "a@b.com")
    page.wait_for_timeout(300)
    folder = trace.save(page, outcome="still on the account form")

    assert trace.posts() == []
    summary = (folder / "summary.txt").read_text(encoding="utf-8")
    assert "submissions: 0" in summary


def test_a_console_error_is_kept(tmp_path, site, page):
    trace = evidence.Evidence(tmp_path / "trace")
    trace.attach(page.context, page)
    page.goto(site, wait_until="domcontentloaded")
    page.evaluate("() => console.error('boom from the app')")
    page.wait_for_timeout(200)
    folder = trace.save(page, outcome="probe")

    console = json.loads((folder / "console.json").read_text(encoding="utf-8"))
    assert any("boom from the app" in row["text"] for row in console)


def test_saving_does_not_raise_when_the_page_is_gone(tmp_path):
    trace = evidence.Evidence(tmp_path / "trace")
    folder = trace.save(None, outcome="page already closed")
    assert folder.is_dir()
    assert (folder / "summary.txt").is_file()


def test_a_password_in_the_saved_dom_is_masked(tmp_path, site, page):
    """The first live trace wrote the account password into page.html.

    A React-style controlled input reflects its value into the attribute, which
    is how `value="..."` ended up on disk. Set it the same way here.
    """
    trace = evidence.Evidence(tmp_path / "trace", secrets=("hunter2!",))
    trace.attach(page.context, page)
    page.goto(site, wait_until="domcontentloaded")
    page.evaluate(
        "() => document.querySelector('#password').setAttribute('value', 'hunter2!')"
    )
    folder = trace.save(page, outcome="filled")

    html = (folder / "page.html").read_text(encoding="utf-8")
    assert "hunter2!" not in html
    assert evidence.MASK in html


def test_a_password_in_the_dom_is_masked_even_when_the_secret_is_unknown(tmp_path, site, page):
    """Second belt: a password field is blanked whatever it holds."""
    trace = evidence.Evidence(tmp_path / "trace")
    trace.attach(page.context, page)
    page.goto(site, wait_until="domcontentloaded")
    page.evaluate(
        "() => document.querySelector('#password').setAttribute('value', 'never-listed-9')"
    )
    folder = trace.save(page, outcome="filled")

    html = (folder / "page.html").read_text(encoding="utf-8")
    assert "never-listed-9" not in html
