"""What actually happened during a browser run, kept on disk.

A screenshot shows what the page looked like. It cannot show whether a request
left the browser, what the server answered, or what the console said. When a
form silently refuses to submit, those are the only facts that separate "the
click never landed" from "the server rejected the values" — and finding out by
re-running against a live tenant is slow, impolite, and often rate-limited.

So a run writes a folder: the network log, console messages, page errors, the
DOM, a screenshot, and a one-page summary you can read in ten seconds.

Secrets are removed on the way in, not on the way out: the register POST for a
Workday account contains the password, and a trace folder that keeps it is a
worse problem than the bug it was written to find.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MASK = "[redacted]"

# Names whose values never reach the disk. Matching is on the bare name, plus
# any name containing "password" (Workday uses verifyPassword, confirmPassword).
_SECRET_NAMES = {
    "pwd",
    "passwd",
    "secret",
    "client_secret",
    "token",
    "access_token",
    "refresh_token",
    "otp",
    "verificationcode",
    "verification_code",
    "securitycode",
}


def _is_secret_name(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered in _SECRET_NAMES or "password" in lowered


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove every known secret value from a string."""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, MASK)
    return text


def redact_url(url: str, secrets: Iterable[str] = ()) -> str:
    """Mask query parameters that carry a secret."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return redact_text(url, secrets)
    if not parts.query:
        return redact_text(url, secrets)
    pairs = [
        (key, MASK if _is_secret_name(key) else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    rebuilt = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(pairs, safe="[]"), parts.fragment)
    )
    return redact_text(rebuilt, secrets)


def redact_body(body: str, secrets: Iterable[str] = ()) -> str:
    """Mask secrets in a JSON or form-encoded request body."""
    if not body:
        return body
    stripped = body.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.dumps(_redact_json(json.loads(body)), ensure_ascii=False)
        except (ValueError, TypeError):
            pass
    if "=" in body:
        try:
            pairs = parse_qsl(body, keep_blank_values=True)
        except ValueError:
            pairs = []
        if pairs:
            masked = [
                (key, MASK if _is_secret_name(key) else value) for key, value in pairs
            ]
            rebuilt = urlencode(masked, safe="[]")
            return redact_text(rebuilt, secrets)
    return redact_text(body, secrets)


def _redact_json(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: MASK if _is_secret_name(str(key)) else _redact_json(value)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_redact_json(item) for item in node]
    return node


class Evidence:
    """Records one browser run into a folder, safely."""

    def __init__(self, directory: Path, *, secrets: Iterable[str] = (), label: str = ""):
        self.directory = Path(directory)
        self.label = label
        self.secrets = tuple(secret for secret in secrets if secret)
        self.started = datetime.now().astimezone()
        self._clock = monotonic()
        self.requests: list[dict[str, Any]] = []
        self.console: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []
        self.notes: list[dict[str, Any]] = []
        self.url = ""
        self.outcome = ""

    # --- recording ------------------------------------------------------

    def attach(self, context, page) -> None:
        """Listen to a page and its context. Never raises from a handler."""
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        context.on("request", self._on_request)
        context.on("response", self._on_response)
        context.on("requestfailed", self._on_request_failed)

    def note(self, kind: str, detail: str = "") -> None:
        """Mark a moment: a click, a step transition, a decision."""
        self.notes.append(
            {
                "t": self._elapsed(),
                "kind": kind,
                "detail": redact_text(str(detail), self.secrets)[:400],
            }
        )

    def _elapsed(self) -> float:
        return round(monotonic() - self._clock, 3)

    def _on_request(self, request) -> None:
        try:
            body = ""
            if request.method not in ("GET", "HEAD"):
                try:
                    body = redact_body(request.post_data or "", self.secrets)[:4000]
                except Exception:  # noqa: BLE001
                    body = ""
            self.requests.append(
                {
                    "t": self._elapsed(),
                    "kind": "request",
                    "method": request.method,
                    "url": redact_url(request.url, self.secrets),
                    "resource_type": request.resource_type,
                    "body": body,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_response(self, response) -> None:
        try:
            request = response.request
            if request.method in ("GET", "HEAD") and response.status < 400:
                return
            self.requests.append(
                {
                    "t": self._elapsed(),
                    "kind": "response",
                    "method": request.method,
                    "url": redact_url(response.url, self.secrets),
                    "status": response.status,
                    "resource_type": request.resource_type,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_request_failed(self, request) -> None:
        try:
            self.failures.append(
                {
                    "t": self._elapsed(),
                    "url": redact_url(request.url, self.secrets),
                    "failure": redact_text(str(request.failure or ""), self.secrets),
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_console(self, message) -> None:
        try:
            if message.type not in ("error", "warning"):
                return
            self.console.append(
                {
                    "t": self._elapsed(),
                    "type": message.type,
                    "text": redact_text(message.text, self.secrets)[:2000],
                }
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_pageerror(self, error) -> None:
        try:
            self.console.append(
                {
                    "t": self._elapsed(),
                    "type": "pageerror",
                    "text": redact_text(str(error), self.secrets)[:2000],
                }
            )
        except Exception:  # noqa: BLE001
            pass

    # --- saving ---------------------------------------------------------

    def save(self, page=None, *, outcome: str = "", step: str = "") -> Path:
        """Write everything out. Returns the folder.

        Nothing here may raise: this runs on the failure path, and losing a
        trace because the page already closed would be the worst outcome.
        """
        self.outcome = redact_text(outcome, self.secrets)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            return self.directory

        if page is not None:
            try:
                self.url = page.url
            except Exception:  # noqa: BLE001
                pass
            try:
                html = page.content()
                (self.directory / "page.html").write_text(html, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            try:
                page.screenshot(path=str(self.directory / "screenshot.png"), full_page=True)
            except Exception:  # noqa: BLE001
                pass

        meta = {
            "label": self.label,
            "started": self.started.isoformat(),
            "finished": datetime.now().astimezone().isoformat(),
            "url": redact_url(self.url, self.secrets),
            "step": step,
            "outcome": self.outcome,
            "notes": self.notes,
            "request_count": len([r for r in self.requests if r["kind"] == "request"]),
            "failure_count": len(self.failures),
            "console_count": len(self.console),
        }
        self._write("meta.json", json.dumps(meta, indent=2, ensure_ascii=False))
        self._write(
            "network.jsonl",
            "\n".join(json.dumps(row, ensure_ascii=False) for row in self.requests),
        )
        self._write("console.json", json.dumps(self.console, indent=2, ensure_ascii=False))
        self._write("failures.json", json.dumps(self.failures, indent=2, ensure_ascii=False))
        self._write("summary.txt", self.summary())
        return self.directory

    def _write(self, name: str, content: str) -> None:
        try:
            (self.directory / name).write_text(content, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # --- the ten-second version -----------------------------------------

    def posts(self) -> list[dict[str, Any]]:
        """Requests that submit data. The first place to look at a dead form."""
        return [
            row
            for row in self.requests
            if row["kind"] == "request" and row["method"] not in ("GET", "HEAD")
        ]

    def summary(self) -> str:
        lines = [f"{self.label or 'run'}: {self.url}"]
        if self.outcome:
            lines.append(f"  outcome: {self.outcome}")
        posts = self.posts()
        lines.append(f"  submissions: {len(posts)}")
        for row in posts:
            status = next(
                (
                    other.get("status")
                    for other in self.requests
                    if other["kind"] == "response"
                    and other["url"] == row["url"]
                    and other["method"] == row["method"]
                ),
                "no response",
            )
            path = urlsplit(row["url"]).path
            lines.append(f"    {row['method']} {path} -> {status}")
            if row["body"]:
                lines.append(f"      body: {row['body'][:300]}")
        if self.failures:
            lines.append(f"  failed requests: {len(self.failures)}")
            for row in self.failures[:5]:
                lines.append(f"    {urlsplit(row['url']).path} {row['failure'][:120]}")
        errors = [row for row in self.console if row["type"] in ("error", "pageerror")]
        if errors:
            lines.append(f"  console errors: {len(errors)}")
            for row in errors[:5]:
                lines.append(f"    {row['text'][:160]}")
        if self.notes:
            lines.append("  moments:")
            for row in self.notes[-8:]:
                lines.append(f"    +{row['t']}s {row['kind']}: {row['detail'][:120]}")
        return "\n".join(lines)


def new_trace(home: Path, label: str, *, secrets: Iterable[str] = ()) -> Evidence:
    """A trace folder for this run: ``<home>/traces/<timestamp>-<label>``."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "run"
    return Evidence(home / "traces" / f"{stamp}-{safe}", secrets=secrets, label=label)
