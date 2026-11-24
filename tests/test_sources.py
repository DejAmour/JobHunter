"""Sources and the probe.

The failure that matters here is a source that silently returns nothing: a
missing aggregator key must be an error, not an empty list that looks like "no
jobs today".
"""

from __future__ import annotations

import pytest

from jobhunter import probe, sources


def test_adzuna_market_is_translated():
    # firms.toml says "uk" like every other entry; Adzuna calls it "gb".
    source = sources.build({"name": "Adzuna UK", "ats": "adzuna", "country": "uk"})
    assert isinstance(source, sources.Adzuna)
    assert source.country == "gb"

    aus = sources.build({"name": "Adzuna AU", "ats": "adzuna", "country": "au"})
    assert aus.country == "au"


def test_missing_aggregator_key_is_an_error_not_an_empty_list():
    adzuna = sources.build({"name": "Adzuna UK", "ats": "adzuna"})
    assert not adzuna.available()
    with pytest.raises(sources.SourceError):
        adzuna.list()

    reed = sources.build({"name": "Reed", "ats": "reed"})
    assert not reed.available()
    with pytest.raises(sources.SourceError):
        reed.list()


def test_unknown_board_type_is_reported():
    with pytest.raises(sources.SourceError):
        sources.build({"name": "Mystery", "ats": "taleo"})


def test_probe_writes_a_usable_toml_block():
    block = probe.as_toml(
        {"name": "Invesco", "ats": "workday", "tenant": "invesco", "wd": "wd1", "site": "IVZ"}
    )
    assert block.startswith("[[firms]]")
    assert 'name = "Invesco"' in block
    assert 'site = "IVZ"' in block
    # Optional keys are omitted rather than written empty.
    assert "country" not in block


def test_probe_names_a_domain_sensibly():
    assert probe._pretty_name("https://www.invesco.com/gb") == "Invesco"
    assert probe._pretty_name("rathbones.com") == "Rathbones"


def test_jobspy_salary_rendering():
    assert sources._salary(55000, 70000, "GBP") == "GBP 55,000–70,000"
    assert sources._salary(55000, 55000, "GBP") == "GBP 55,000"
    assert sources._salary(None, 70000, "GBP") == "GBP 70,000"
    assert sources._salary(None, None, "") == ""
    # pandas gives float('nan') for missing numbers
    assert sources._salary(float("nan"), float("nan"), "GBP") == ""


def test_jobspy_cell_cleaning():
    assert sources._clean_cell(float("nan")) == ""
    assert sources._clean_cell(None) == ""
    assert sources._clean_cell("  Acme  ") == "Acme"


def test_oracle_reads_the_requisition_list_and_builds_job_urls(monkeypatch):
    calls = []

    def fake_json(url, *, body=None, retries=4):
        calls.append(url)
        return {
            "items": [
                {
                    "TotalJobsCount": 2,
                    "requisitionList": [
                        {
                            "Id": "210811920",
                            "Title": "Investment Analyst",
                            "PrimaryLocation": "LONDON, United Kingdom",
                            "PostedDate": "2026-11-24",
                            "Department": "Investments",
                            "ShortDescriptionStr": "<p>Join the team.</p>",
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(sources, "_json", fake_json)
    source = sources.build(
        {"name": "JPMorgan", "ats": "oracle", "host": "jpmc.fa.oraclecloud.com", "site": "CX_1001"},
        ["investment"],
    )
    postings = source.list()

    assert len(postings) == 1
    posting = postings[0]
    assert posting.title == "Investment Analyst"
    assert posting.url == (
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210811920"
    )
    assert posting.posted_at == "2026-11-24"
    assert "Join the team." in posting.description
    # The target title is a keyword, not a full board crawl.
    assert "keyword%3Dinvestment" in calls[0] or "keyword=investment" in calls[0]


def test_oracle_detail_asks_by_requisition_id(monkeypatch):
    seen = []

    def fake_json(url, *, body=None, retries=4):
        seen.append(url)
        return {"items": [{"ExternalDescriptionStr": "<p>The full advert.</p>"}]}

    monkeypatch.setattr(sources, "_json", fake_json)
    source = sources.build(
        {"name": "Schroders", "ats": "oracle", "host": "ekbq.fa.em2.oraclecloud.com", "site": "CX_2"}
    )
    posting = sources.Posting(
        company="Schroders",
        title="Analyst",
        location="London",
        url="https://ekbq.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2/job/42",
        source="oracle",
    )
    text = source.detail(posting)
    assert text == "The full advert."
    assert "recruitingCEJobRequisitionDetails" in seen[0]
    assert "Id%3D%2242%22" in seen[0] or 'Id=%2242%22' in seen[0]


def test_oracle_is_detected_from_a_candidate_experience_url():
    entry = probe._match(
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/1"
    )
    assert entry == {
        "name": "",
        "ats": "oracle",
        "host": "jpmc.fa.oraclecloud.com",
        "site": "CX_1001",
    }
