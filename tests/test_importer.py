"""Importing saved pages.

LinkedIn and Indeed cannot be read automatically, so the value here is that the
parser is honest: it extracts what the saved markup actually contains and says so
when the markup is not what it expects.
"""

from __future__ import annotations

from jobhunter import importer

LINKEDIN = """
<li>
  <div class="base-card relative w-full base-card--link base-search-card base-search-card--link job-search-card">
    <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/investment-analyst-at-acme-4012345678?refId=x">
      <span class="sr-only">Investment Analyst</span>
    </a>
    <div class="base-search-card__info">
      <h3 class="base-search-card__title">Investment Analyst</h3>
      <h4 class="base-search-card__subtitle">
        <a class="hidden-nested-link" href="/company/acme">Acme Asset Management</a>
      </h4>
      <div class="base-search-card__metadata">
        <span class="job-search-card__location">London, England, United Kingdom</span>
      </div>
    </div>
  </div>
</li>
<li>
  <div class="base-card base-search-card">
    <h3 class="base-search-card__title">Portfolio Analyst</h3>
    <h4 class="base-search-card__subtitle">Beta Partners</h4>
    <span class="job-search-card__location">Edinburgh, Scotland, United Kingdom</span>
    <a class="base-card__full-link" href="https://uk.linkedin.com/jobs/view/portfolio-analyst-999?x=1">x</a>
  </div>
</li>
"""

JSONLD = """
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Credit Risk Analyst",
 "hiringOrganization": {"name": "Gamma Bank"},
 "jobLocation": {"address": {"addressLocality": "Manchester", "addressCountry": "GB"}},
 "url": "https://example.com/job/1"}
</script>
"""


def test_linkedin_cards_are_read():
    found = importer.parse(LINKEDIN, "linkedin")
    assert [item.title for item in found] == ["Investment Analyst", "Portfolio Analyst"]
    assert found[0].company == "Acme Asset Management"
    assert found[0].location == "London, England, United Kingdom"
    # Query strings are dropped so the same advert seen twice compares equal.
    assert found[0].url == "https://uk.linkedin.com/jobs/view/investment-analyst-at-acme-4012345678"
    assert found[1].url == "https://uk.linkedin.com/jobs/view/portfolio-analyst-999"


def test_jsonld_is_preferred_and_understood():
    found = importer.parse(JSONLD, "auto")
    assert len(found) == 1
    assert found[0].title == "Credit Risk Analyst"
    assert found[0].company == "Gamma Bank"
    assert found[0].location == "Manchester, GB"


def test_unparseable_page_says_nothing_rather_than_guessing():
    assert importer.parse("<html><body>hello</body></html>") == []


def test_duplicate_cards_collapse():
    found = importer.parse(LINKEDIN + LINKEDIN, "linkedin")
    assert len(found) == 2
