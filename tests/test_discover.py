"""Discovery filters.

These are the checks that decide whether a posting is ever seen by a human, so
the failure modes matter more than the happy path: a location matcher that says
"New York" is in the UK, or a company filter that lets Jane Street through, is
worse than no discovery at all.
"""

from __future__ import annotations

from jobhunter import discover, sources


def test_london_is_uk():
    assert discover.country_of("London") == "uk"
    assert discover.country_of("London, United Kingdom") == "uk"
    assert discover.country_of("Edinburgh, Scotland") == "uk"


def test_new_york_is_not_uk():
    # Regression: "York" is a UK word, so "New York" used to pass as British.
    assert discover.country_of("New York") == ""
    assert discover.country_of("Massachusetts") == ""
    assert discover.country_of("Fort Worth; New York") == ""


def test_multi_city_keeps_the_in_scope_city():
    assert discover.country_of("London; New York") == "uk"
    assert discover.country_of("Boston; London") == "uk"


def test_australia():
    assert discover.country_of("Melbourne VIC") == "au"
    assert discover.country_of("Sydney, Australia") == "au"
    assert discover.country_of("Anywhere", "au") == "au"


def test_unplaceable_is_rejected():
    assert discover.country_of("") == ""
    assert discover.country_of("2 Locations") == ""
    assert discover.country_of("Bangalore") == ""


def test_birmingham_is_excluded_but_bristol_is_not():
    filters = discover.Filters(exclude_locations=("Birmingham",))
    assert discover.is_excluded_location("Birmingham, England", filters)
    assert not discover.is_excluded_location("Bristol, England", filters)
    assert not discover.is_excluded_location("London", filters)


def test_tier_one_firms_are_excluded():
    filters = discover.Filters(exclude_companies=("Jane Street", "Citadel", "HRT"))
    assert discover.is_excluded_company("Jane Street", filters)
    assert discover.is_excluded_company("Citadel LLC", filters)
    assert not discover.is_excluded_company("Man Group", filters)


def test_title_matching_is_on_phrases_not_single_words():
    assert discover.title_matches("Investment Analyst", discover.TITLE_TERMS)
    assert discover.title_matches("Junior Portfolio Manager", discover.TITLE_TERMS)
    assert not discover.title_matches("Software Engineer", discover.TITLE_TERMS)
    assert not discover.title_matches("Head of Compliance Operations", discover.TITLE_TERMS)


def test_seniority_flag():
    assert discover.is_senior("Head of Investment and Market Risk Oversight")
    assert discover.is_senior("Managing Director, Credit")
    assert not discover.is_senior("Investment Analyst")
    assert not discover.is_senior("Graduate Investment Analyst")


def test_country_config_accepts_gb():
    filters = discover.Filters.from_config({"search": {"countries": ["GB", "AU"]}})
    assert "uk" in filters.allow_countries
    assert "au" in filters.allow_countries


def test_html_is_stripped_before_entities_are_decoded():
    # Regression: Greenhouse returns escaped markup, so stripping tags first left
    # "<div class=...>" in the description and the screeners read it as prose.
    text = sources._strip("&lt;p&gt;Salary: &amp;pound;55,000&lt;/p&gt;")
    assert "<p>" not in text
    assert "Salary" in text
    assert "£55,000" in text
