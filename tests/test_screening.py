from jobhunter import screening

BASE = """
We are hiring a Senior Backend Engineer to join the payments team.

Salary: £72,000 - £88,000 depending on experience.

What you will do
- Design and build services in Python and Go
- Own reliability for the payments API
- Work with product and data teams on new rails

What we are looking for
- You must have at least 5 years of professional software engineering experience
- Strong experience with Python and distributed systems
- Experience with PostgreSQL and message queues such as Kafka
- Proficiency with Kubernetes and Terraform
- Excellent communication with non-technical stakeholders

This role is hybrid, with two days a week in our London office.
"""

CV = """
Senior backend engineer with 7 years of experience. Python, Go, PostgreSQL,
Kafka, Kubernetes, Terraform, distributed systems, payments, reliability,
API design, stakeholder communication.
"""


def test_clean_posting_is_apply():
    report = screening.screen(BASE, CV, screening.Candidate(right_to_work=True))
    assert report.decision == "apply", report.summary()
    assert report.coverage is not None and report.coverage > 0.5
    assert report.salary == ("GBP", 72000, 88000)


def test_sponsorship_blocks_only_when_needed():
    text = BASE + "\nWe are unable to offer visa sponsorship for this role."
    needing = screening.screen(text, CV, screening.Candidate(needs_sponsorship=True, right_to_work=True))
    assert needing.decision == "exclude"
    assert any("sponsorship" in b.name for b in needing.blockers)

    settled = screening.screen(text, CV, screening.Candidate(needs_sponsorship=False, right_to_work=True))
    assert settled.decision == "apply", settled.summary()


def test_on_site_blocks_when_remote_only():
    text = BASE.replace("This role is hybrid", "This role is fully on-site")
    report = screening.screen(text, CV, screening.Candidate(remote_only=True, right_to_work=True))
    assert report.decision == "exclude"
    assert any(b.name == "not-remote" for b in report.blockers)


def test_salary_below_floor_blocks():
    report = screening.screen(BASE, CV, screening.Candidate(salary_floor=95_000, currency="GBP", right_to_work=True))
    assert report.decision == "exclude"
    assert any(b.name == "salary-below-floor" for b in report.blockers)


def test_clearance_blocks_when_not_held():
    text = BASE + "\nYou must hold active SC clearance to start."
    report = screening.screen(text, CV, screening.Candidate(right_to_work=True))
    assert report.decision == "exclude"
    assert any(b.name == "clearance" for b in report.blockers)

    holding = screening.screen(text, CV, screening.Candidate(clearances=("SC",), right_to_work=True))
    assert holding.decision == "apply", holding.summary()


def test_unknown_right_to_work_asks():
    text = BASE + "\nYou must have the right to work in the UK."
    report = screening.screen(text, CV)
    assert report.decision == "ask"
    assert any(c.name == "right-to-work" for c in report.checks)


def test_missing_requirements_are_reported():
    thin = "Senior backend engineer. Python and PostgreSQL."
    report = screening.screen(BASE, thin, screening.Candidate(right_to_work=True))
    assert report.missing
    assert report.coverage is not None and report.coverage < 1.0


def test_no_cv_means_no_coverage():
    report = screening.screen(BASE, "", screening.Candidate(right_to_work=True))
    assert report.coverage is None
