"""Following an aggregator's Apply link, and what the filler says when it cannot.

The failure this replaces: a saved Adzuna job, a click on Fill, and "no filler for
this site yet" — which is true, tells you nothing, and reads like a bug in a page
that plainly has an Apply button.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobhunter import fill, resolve

playwright = pytest.importorskip("playwright.sync_api")


def page_file(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(f"<!doctype html><html><body>{body}</body></html>", encoding="utf-8")
    return path.as_uri()


def test_an_aggregator_url_is_recognised():
    assert resolve.looks_like_aggregator("https://www.adzuna.co.uk/jobs/details/1")
    assert resolve.looks_like_aggregator("https://www.reed.co.uk/jobs/x/1")
    assert resolve.looks_like_aggregator("https://uk.indeed.com/viewjob?jk=1")
    # An employer's own board is never guessed at.
    assert not resolve.looks_like_aggregator("https://job-boards.greenhouse.io/acme/jobs/1")
    assert not resolve.looks_like_aggregator("https://acme.wd3.myworkdayjobs.com/en-US/x/job/1")


def test_a_closed_listing_is_reported_as_closed(tmp_path):
    url = page_file(tmp_path, "listing.html", "<h1>Credit Risk Analyst</h1><p>Unfortunately, this job is no longer available</p>")
    outcome = resolve.resolve(url, headless=True)
    assert outcome.closed is True
    assert "no longer available" in outcome.note


def test_the_apply_link_is_followed_to_the_form(tmp_path):
    form = page_file(tmp_path, "form.html", '<h1>Application</h1><input name="firstname">')
    listing = page_file(
        tmp_path,
        "listing.html",
        f'<h1>Credit Risk Analyst</h1><a href="{form}">Apply for this job</a>',
    )
    outcome = resolve.resolve(listing, headless=True)
    assert outcome.closed is False
    assert outcome.final_url.endswith("form.html")
    assert "followed Apply" in outcome.note


def test_a_listing_with_no_apply_control_says_so(tmp_path):
    url = page_file(tmp_path, "listing.html", "<h1>Credit Risk Analyst</h1><p>No button here</p>")
    outcome = resolve.resolve(url, headless=True)
    assert outcome.final_url == url
    assert "no Apply control" in outcome.note


def test_a_closed_listing_stops_the_fill_with_a_reason(monkeypatch, tmp_path):
    """The wiring: run() must not fall through to 'no filler for this site'."""
    monkeypatch.setattr(resolve, "looks_like_aggregator", lambda url: True)
    monkeypatch.setattr(
        resolve,
        "resolve",
        lambda url, **kwargs: resolve.Resolution(url=url, closed=True, note="gone"),
    )
    report = fill.run("https://www.adzuna.co.uk/jobs/details/1", fill.Values(), headless=True)
    assert report.error
    assert "listing has closed" in report.error
    assert "no filler" not in report.error


def test_an_unknown_destination_names_the_site_and_the_url(monkeypatch):
    monkeypatch.setattr(resolve, "looks_like_aggregator", lambda url: True)
    monkeypatch.setattr(
        resolve,
        "resolve",
        lambda url, **kwargs: resolve.Resolution(
            url=url, final_url="https://jobs.someagency.test/apply/123", note="followed"
        ),
    )
    report = fill.run("https://www.reed.co.uk/jobs/x/1", fill.Values(), headless=True)
    assert "jobs.someagency.test" in report.error
    assert "https://jobs.someagency.test/apply/123" in report.error


def test_a_followed_link_to_a_known_board_is_filled(monkeypatch):
    """Adzuna -> a board we support: the run continues on that board's filler."""
    fixture = (
        Path(__file__).resolve().parents[1]
        / "vendor" / "jobappagent" / "fixtures" / "lever" / "index.html"
    ).resolve().as_uri()
    real = fill.detect_ats
    monkeypatch.setattr(fill, "detect_ats", lambda url: "lever" if url == fixture else real(url))
    monkeypatch.setattr(resolve, "looks_like_aggregator", lambda url: True)
    monkeypatch.setattr(
        resolve,
        "resolve",
        lambda url, **kwargs: resolve.Resolution(url=url, final_url=fixture, note="followed"),
    )

    class Recorder(fill.Lever):
        def fill_form(self, page, values, ctx, report):
            report.filled.append("reached")

    monkeypatch.setitem(fill.FILLERS, "lever", Recorder)
    report = fill.run("https://www.adzuna.co.uk/jobs/details/1", fill.Values(), headless=True)
    assert "reached" in report.filled
    assert any("followed the listing" in note for note in report.notes)


def test_a_board_that_keeps_the_application_on_its_own_page_is_named(monkeypatch):
    """Reed's Apply button opens a Reed sign-in, not the employer's form.

    The first version of this said "the application happens on www.reed.co.uk,
    which has no filler yet", which reads like a missing adapter rather than a
    board that never exposes a form.
    """
    url = "https://www.reed.co.uk/jobs/graduate-credit-risk-analyst/57520923"
    monkeypatch.setattr(resolve, "looks_like_aggregator", lambda candidate: True)
    monkeypatch.setattr(
        resolve,
        "resolve",
        lambda candidate, **kwargs: resolve.Resolution(
            url=url, final_url=url, note="the Apply control did not lead anywhere"
        ),
    )
    report = fill.run(url, fill.Values(), headless=True)
    assert report.error
    assert "Reed runs applications behind a Reed account" in report.error
    assert "no employer form" in report.error
    assert "no filler" not in report.error


def test_an_unlisted_board_still_says_which_host_it_is(monkeypatch):
    url = "https://www.adzuna.co.uk/jobs/details/1"
    monkeypatch.setattr(resolve, "looks_like_aggregator", lambda candidate: True)
    monkeypatch.setattr(
        resolve,
        "resolve",
        lambda candidate, **kwargs: resolve.Resolution(
            url=url, final_url="https://careers.example.test/apply", note="followed"
        ),
    )
    report = fill.run(url, fill.Values(), headless=True)
    assert "careers.example.test" in report.error
