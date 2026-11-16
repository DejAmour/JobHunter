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
