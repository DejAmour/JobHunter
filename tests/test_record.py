"""Recording a live form as a fixture: the pure parts, and what must be kept.

ADR 5 says a fixture should be the tenant's own markup. These tests cover the
transformation that makes a recording replayable offline, and the naming that
keeps two forms at one ATS apart.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobhunter import record

ROOT = Path(__file__).resolve().parents[1]
RECORDED = ROOT / "tests" / "fixtures" / "recorded"


def test_the_fixture_name_keeps_the_employer_and_the_posting():
    slug = record.slug_for(
        "https://jobs.ashbyhq.com/neudata/916b4157-e176-4e11-8504-042d4b8fc26d",
        "ashby",
    )
    assert slug.startswith("ashby-")
    assert "neudata" in slug
    assert "916b4157" in slug


def test_two_postings_at_one_employer_do_not_collide():
    first = record.slug_for("https://jobs.ashbyhq.com/quantum/75dae293", "ashby")
    second = record.slug_for("https://jobs.ashbyhq.com/quantum/24e8d551", "ashby")
    assert first != second


def test_scripts_and_iframes_are_removed_so_the_fixture_needs_no_network():
    html = (
        "<html><head><script>fetch('/api')</script>"
        "<link rel='preload' href='https://cdn.example/x.js'></head>"
        "<body><form><input name='email'></form>"
        "<iframe src='https://captcha.example'></iframe></body></html>"
    )
    cleaned = record.strip_for_replay(html)
    assert "<script" not in cleaned
    assert "fetch(" not in cleaned
    assert "<iframe" not in cleaned
    assert "rel='preload'" not in cleaned
    # The form itself is the whole point.
    assert "<input name='email'>" in cleaned


def test_inline_style_is_kept_because_it_hides_and_shows_controls():
    html = "<div style='display:none'><input name='x'></div>"
    assert "display:none" in record.strip_for_replay(html)


def test_the_fixture_says_where_it_came_from():
    html = record.annotate("<html><head></head><body></body></html>", url="https://x.test", ats="ashby", captured="2026-11-24T12:00:00")
    assert "recorded from https://x.test" in html
    assert "ashby" in html


def test_every_recorded_fixture_has_its_provenance():
    metas = sorted(RECORDED.glob("*/*.json"))
    assert metas, "no recordings committed"
    for meta_path in metas:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["url"].startswith("http"), meta_path
        assert meta["ats"], meta_path
        assert meta["captured"], meta_path
        fixture = meta_path.with_suffix(".html")
        assert fixture.is_file(), fixture
        text = fixture.read_text(encoding="utf-8")
        assert "recorded from" in text[:600], fixture
        assert meta["control_count"] > 0, meta_path


def test_a_recording_lists_the_controls_a_filler_will_look_for():
    meta = json.loads(
        next((RECORDED / "workable").glob("*.json")).read_text(encoding="utf-8")
    )
    names = {control["name"] for control in meta["controls"] if control["name"]}
    assert {"firstname", "lastname", "email", "phone"} <= names
    assert any(control["type"] == "file" for control in meta["controls"])
    line = record.inventory_line(meta["controls"][0])
    assert "input" in line
