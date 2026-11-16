"""Choosing the CV for a posting, and building the pack.

The choice is the part worth testing: a credit role must not get the asset
management CV because the employer happens to be an asset manager.
"""

from __future__ import annotations

from jobhunter import apply


def test_credit_title_beats_asset_management_body():
    # Man Group's advert mentions asset management and portfolio throughout;
    # the title is the only part that says what the job actually is.
    body = "Man Group is a global asset management firm. Our portfolio managers and fund research team."
    variant, _ = apply.choose_variant("Credit Analyst – European Financials", body)
    assert variant == "Credit Risk"


def test_risk_title():
    assert apply.choose_variant("Investment Risk Analyst", "")[0] == "Risk"
    assert apply.choose_variant("Performance and Attribution Analyst", "")[0] == "Risk"


def test_default_is_the_truthful_baseline():
    assert apply.choose_variant("Investment Analyst", "")[0] == "Asset Management"
    assert apply.choose_variant("Portfolio Manager", "")[0] == "Asset Management"


def test_body_is_used_when_the_title_says_nothing():
    variant, _ = apply.choose_variant("Analyst", "This credit fund invests in high yield bonds and issuers.")
    assert variant == "Credit Risk"


def test_cv_filenames_match_the_build():
    assert apply.cv_file("Asset Management", "uk") == "Jed Dahlke CV.pdf"
    assert apply.cv_file("Risk", "uk") == "Jed Dahlke CV Risk.pdf"
    assert apply.cv_file("Risk", "au") == "Jed Dahlke CV Risk (AU).pdf"
    assert apply.cv_file("Credit Risk", "au") == "Jed Dahlke CV Credit Risk (AU).pdf"


def test_evidence_comes_from_the_cv_not_from_thin_air():
    cv = (
        "Supported the management of a c.£80m model portfolio service comprising 16 portfolios.\n"
        "Conducted fund and manager research, asset allocation analysis and stress testing.\n"
        "Won a university basketball competition.\n"
    )
    lines = apply.evidence_from_cv(cv, "We need portfolio management and asset allocation experience")
    assert lines
    assert all("basketball" not in line for line in lines)
    assert any("portfolio" in line.lower() for line in lines)
