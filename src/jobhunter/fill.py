"""Fill an employer's application form, and stop before submitting.

The order of trust here is the whole design:

* The **answer bank** in config is the only source of values. A posting, a page,
  or a form cannot tell this what to write.
* A field with no answer is **left empty and reported**, never guessed. An empty
  required field fails loudly at the employer's own validation, which is the
  right place for it to fail.
* **Nothing is submitted** unless ``--submit`` is passed. The default run fills
  the form, screenshots it, and hands control back.

Tested against the fixtures in ``vendor/jobappagent/fixtures``, which are real
Greenhouse and Lever markup, so no test ever touches an employer.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import db

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ATS_HOSTS = (
    ("greenhouse", ("greenhouse.io", "grnh.se")),
    ("lever", ("lever.co",)),
    ("workday", ("myworkdayjobs.com", "workday.com")),
    ("smartrecruiters", ("smartrecruiters.com",)),
    ("workable", ("workable.com",)),
    ("ashby", ("ashbyhq.com",)),
)


def detect_ats(url: str) -> str:
    """Which applicant tracking system a URL points at, or ''."""
    lowered = (url or "").lower()
    for name, hosts in ATS_HOSTS:
        if any(host in lowered for host in hosts):
            return name
    return ""


@dataclass
class Values:
    """Everything the filler is allowed to write, already resolved."""

    first_name: str = ""
    last_name: str = ""
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    country: str = ""
    org: str = ""
    linkedin: str = ""
    cv_path: str = ""
    address: str = ""
    city: str = ""
    postcode: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "first_name": self.first_name,
            "last_name": self.last_name,
            "name": self.full_name,
            "email": self.email,
            "phone": self.phone,
            "location": self.location,
            "country": self.country,
            "org": self.org,
            "linkedin": self.linkedin,
            "resume": self.cv_path,
            "address": self.address,
            "city": self.city,
            "postcode": self.postcode,
        }

    def empty(self) -> list[str]:
        return [key for key, value in self.as_dict().items() if not value]


def values_from(config: dict, *, cv_path: str, org: str = "", market: str = "uk") -> Values:
    profile = config.get("profile", {})
    answers = config.get("answers", {})

    full_name = str(profile.get("name", "")).strip()
    parts = full_name.split()
    first = parts[0] if parts else ""
    last = " ".join(parts[1:]) if len(parts) > 1 else ""

    countries = {"uk": "United Kingdom", "au": "Australia"}
    return Values(
        first_name=first,
        last_name=last,
        full_name=full_name,
        email=str(profile.get("email", "") or answers.get("email", "")),
        phone=str(answers.get("phone", "") or profile.get("phone", "")),
        location=str(answers.get("location", "")),
        country=str(answers.get("country", "") or countries.get(market, "")),
        org=org,
        # Deliberately from config only: if there is no LinkedIn URL there is no
        # LinkedIn URL, and inventing one would be a lie in a form.
        linkedin=str(answers.get("linkedin", "")),
        cv_path=cv_path,
        address=str(answers.get("address", "")),
        city=str(answers.get("city", "")),
        postcode=str(answers.get("postcode", "")),
    )


@dataclass
class FillReport:
    url: str
    ats: str
    filled: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    empty_answers: list[str] = field(default_factory=list)
    required_left: list[str] = field(default_factory=list)
    questions_answered: list[tuple[str, str]] = field(default_factory=list)
    questions_review: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    sign_in: str = ""
    screenshot: str = ""
    trace_path: str = ""
    submitted: bool = False
    error: str = ""

    def dedupe(self) -> None:
        """Each field is reported once, in the order it was handled.

        Both the Workday widget filler and the generic question engine see the
        same controls on Application Questions, so without this every field
        appears twice and the report reads as if twice as much went wrong.
        """
        seen: set[str] = set()
        filled: list[str] = []
        for name in self.filled:
            if name not in seen:
                seen.add(name)
                filled.append(name)
        self.filled = filled

        seen = set()
        empty: list[str] = []
        for name in self.empty_answers:
            if name not in seen:
                seen.add(name)
                empty.append(name)
        self.empty_answers = empty

        seen = set()
        review: list[tuple[str, str]] = []
        filled = " | ".join(self.filled)
        for question, reason in self.questions_review:
            if question in seen:
                continue
            # A field the widget filler answered must not also appear as a
            # question it could not answer: the generic engine sees the same
            # controls and reports failures for fields already filled.
            head = question[:40]
            if head and head in filled:
                continue
            seen.add(question)
            review.append((question, reason))
        self.questions_review = review

        # Every radio in a required group is marked required, so the page names
        # one unanswered question twice.
        seen = set()
        required: list[str] = []
        for label in self.required_left:
            if label in seen:
                continue
            seen.add(label)
            required.append(label)
        self.required_left = required

    def summary(self) -> str:
        lines = [f"{self.ats or 'unknown ats'}: {self.url}"]
        if self.error:
            # Keep going: the filled/empty lists are usually what explains it.
            lines.append(f"  error: {self.error}")
        lines.append(f"  filled:  {', '.join(self.filled) or 'nothing'}")
        if self.steps:
            lines.append(f"  steps:   {' -> '.join(self.steps)}")
        if self.sign_in:
            lines.append(f"  account: {self.sign_in}")
        if self.skipped:
            lines.append(f"  skipped: {', '.join(self.skipped)}")
        if self.empty_answers:
            lines.append(f"  no answer for: {', '.join(self.empty_answers)}")
        if self.questions_answered:
            lines.append("  questions answered:")
            for question, value in self.questions_answered:
                shown = value if len(value) <= 70 else value[:67] + "..."
                lines.append(f"    {question[:58]} -> {shown}")
        if self.questions_review:
            lines.append("  questions left for you:")
            for question, reason in self.questions_review:
                lines.append(f"    {question[:64]} ({reason})")
        if self.notes:
            lines.append("  notes:")
            for note in self.notes:
                lines.append(f"    {note[:140]}")
        if self.required_left:
            lines.append(f"  still required: {', '.join(self.required_left)}")
        lines.append(f"  submitted: {'yes' if self.submitted else 'no'}")
        if self.screenshot:
            lines.append(f"  screenshot: {self.screenshot}")
        if self.trace_path:
            lines.append(f"  trace: {self.trace_path}")
        return "\n".join(lines)


# --- per-ATS fillers -----------------------------------------------------


@dataclass
class Login:
    """What a filler may need to get past an account wall.

    Passing four loose parameters (email, password, create, on_code) was how
    ``run`` grew a signature that only one ATS could use. This is the whole of
    what any of them can need.
    """

    email: str = ""
    password: str = ""
    create: bool = False
    on_code: object | None = None


class Ats:
    """One applicant-tracking system's form.

    The contract ``run`` uses, and the same shape ``sources.Source`` has on the
    discovery side: a small interface, a registry, and a factory, so adding an
    ATS is a subclass rather than a copy of six loops and two string comparisons
    in ``run``.

    ``fill`` returns the report; ``fill_form`` is what a subclass implements for
    a single page. A wizard overrides ``fill`` instead, because it owns the loop.
    """

    name = "base"

    #: does this ATS put an account wall in front of the form?
    needs_account = False

    #: the control that sends the application, when the site has a normal one
    SUBMIT = "button[type='submit'], #btn-submit, [data-testid='submit-application']"

    def fill(
        self,
        page,
        values: "Values",
        ctx=None,
        *,
        advance: bool = False,
        login: Login | None = None,
    ) -> "FillReport":
        report = FillReport(url=page.url, ats=self.name)
        note = self.prepare(page)
        if note:
            report.notes.append(note)
            report.url = page.url
        self.fill_form(page, values, ctx, report)
        return report

    def prepare(self, page) -> str:
        """Reveal the form if the page is a posting with an Apply control.

        Returns a note for the report, or ``"".`` A single-page ATS usually needs
        nothing here; the ones that put the form behind a link implement it.
        """
        return ""

    def fill_form(self, page, values: "Values", ctx, report: "FillReport") -> None:
        """Fill whatever a single page shows. Subclasses must implement this."""
        raise NotImplementedError

    def submit(self, page) -> str:
        """Press the final submit control. Empty on success, a reason on failure."""
        button = page.query_selector(self.SUBMIT)
        if button is None:
            return "no submit control found"
        before_url = page.url
        before_page = _page_signature(page)
        _click(page, button)
        if _until_page_moves(page, before_url, before_page):
            return ""
        # A click that was dispatched is not a submission. The ledger is only
        # evidence if it means "the page moved".
        return "the submit control was clicked but the page did not move; not recorded as sent"


class Greenhouse(Ats):
    name = "greenhouse"

    # Simple text inputs, by the ids the plugin documents.
    TEXT = {
        "first_name": "#first_name",
        "last_name": "#last_name",
        "email": "#email",
        "phone": "#phone",
        "location": "#candidate-location, #location",
        "linkedin": "#linkedin, input[name='urls[LinkedIn]']",
    }
    SELECT = {}
    FILE = {"resume": "#resume"}

    def fill_form(self, page, values: Values, ctx, report: FillReport) -> None:
        self._text(page, values, report)
        self._select(page, values, report)
        self._country(page, values, report)
        self._file(page, values, report)
        if ctx is not None:
            fill_questions(
                page,
                ctx,
                'input[id^="question_"], select[id^="question_"], textarea[id^="question_"]',
                report,
                # The current form marks its required fields with an asterisk in
                # the label, not with the required attribute, so the page's own
                # check cannot be trusted to name what is still blank.
                report_unmatched=True,
            )

    def _text(self, page, values, report):
        data = values.as_dict()
        for key, selector in self.TEXT.items():
            value = data.get(key, "")
            try:
                element = page.query_selector(selector)
            except Exception:  # noqa: BLE001 - a bad selector is not fatal
                element = None
            if element is None:
                continue
            if not value:
                report.empty_answers.append(key)
                continue
            element.fill(value)
            report.filled.append(key)

    def _select(self, page, values, report):
        data = values.as_dict()
        for key, selector in self.SELECT.items():
            value = data.get(key, "")
            element = page.query_selector(selector)
            if element is None:
                continue
            if not value:
                report.empty_answers.append(key)
                continue
            try:
                element.select_option(label=value)
                report.filled.append(key)
            except Exception:  # noqa: BLE001
                report.skipped.append(f"{key} (not in list)")

    def _country(self, page, values, report):
        """Country is a searchable combobox on the current form, a <select> on old ones."""
        element = page.query_selector("#country")
        if element is None:
            return
        if not values.country:
            report.empty_answers.append("country")
            return
        if element.evaluate("(el) => el.tagName.toLowerCase()") == "select":
            try:
                element.select_option(label=values.country)
                report.filled.append("country")
            except Exception:  # noqa: BLE001
                report.skipped.append("country (not in list)")
            return
        if (element.get_attribute("role") or "").lower() != "combobox":
            return
        chosen = _combo(page, element, values.country)
        if chosen and values.country.lower() in chosen.lower():
            report.filled.append("country")
        else:
            report.skipped.append(f"country (chose {chosen or 'nothing'})")

    def _file(self, page, values, report):
        data = values.as_dict()
        for key, selector in self.FILE.items():
            path = data.get(key, "")
            element = page.query_selector(selector)
            if element is None:
                continue
            if not path or not Path(path).is_file():
                report.empty_answers.append(key)
                continue
            element.set_input_files(path)
            time.sleep(1.5)  # Greenhouse parses the CV and may repopulate fields
            report.filled.append(key)


class Lever(Ats):
    name = "lever"

    TEXT = {
        "name": "input[name='name']",
        "email": "input[name='email']",
        "phone": "input[name='phone']",
        "org": "input[name='org']",
    }
    FILE = {"resume": "input[name='resume'], #resume-upload-input"}

    def fill_form(self, page, values: Values, ctx, report: FillReport) -> None:
        data = values.as_dict()
        for key, selector in self.TEXT.items():
            value = data.get(key, "")
            element = page.query_selector(selector)
            if element is None:
                continue
            if not value:
                report.empty_answers.append(key)
                continue
            element.fill(value)
            report.filled.append(key)

        # Lever's location is a type-ahead; typing without choosing a suggestion
        # leaves the field invalid, so the suggestion is clicked.
        location = page.query_selector("#location-input")
        if location is not None:
            if not values.location:
                report.empty_answers.append("location")
            else:
                location.fill(values.location)
                time.sleep(0.8)
                suggestion = page.query_selector("#location-0")
                if suggestion is not None:
                    suggestion.click()
                    time.sleep(0.4)
                    report.filled.append("location")
                else:
                    report.skipped.append("location (no suggestion appeared)")

        for key, selector in self.FILE.items():
            path = data.get(key, "")
            element = page.query_selector(selector)
            if element is None:
                continue
            if not path or not Path(path).is_file():
                report.empty_answers.append(key)
                continue
            element.set_input_files(path)
            time.sleep(1.5)
            report.filled.append(key)

        if ctx is not None:
            fill_questions(
                page,
                ctx,
                "input[name^='cards['], select[name^='cards['], textarea[name^='cards[']",
                report,
            )


class Workable(Ats):
    """Workable's apply page, recorded from ``apply.workable.com`` on 2026-11-24.

    The form is one page under ``/apply/``; the posting page links to it and the
    recorder follows that link. Two things are not what the markup suggests:

    * ``#address`` is the only visible address control. ``city``, ``postcode``
      and ``country`` are one-pixel inputs the page's type-ahead fills in, so
      writing to them would set a value the page never reads.
    * question groups are ``fieldset[role=radiogroup][data-ui^='QA_']`` with the
      question in ``aria-labelledby`` and no legend, and the radio itself is
      ``aria-hidden`` behind a clickable wrapper. Both are handled by the shared
      question engine.
    """

    name = "workable"

    TEXT = {
        "first_name": "#firstname",
        "last_name": "#lastname",
        "email": "#email",
        "phone": "input[name='phone'], #input_phone",
    }
    FILE = {"resume": "input[type='file'][data-ui='resume'], input[type='file']"}
    SUBMIT = "button[data-ui='apply-button'], button[type='submit']"

    def prepare(self, page) -> str:
        """The posting page links to ``/apply/``; follow it when it is showing."""
        if page.query_selector("input[name='firstname']") is not None:
            return ""
        link = page.query_selector("a[href*='/apply/']")
        if link is None:
            return ""
        href = link.get_attribute("href") or ""
        try:
            link.click(timeout=5_000)
        except Exception:  # noqa: BLE001 - an overlay may swallow the click
            if not href:
                return ""
            page.goto(
                page.evaluate("(href) => new URL(href, location.href).href", href),
                wait_until="domcontentloaded",
                timeout=45_000,
            )
        page.wait_for_timeout(1_500)
        return "opened the application page"

    def fill_form(self, page, values: Values, ctx, report: FillReport) -> None:
        data = values.as_dict()
        for key, selector in self.TEXT.items():
            value = data.get(key, "")
            element = page.query_selector(selector)
            if element is None:
                continue
            if not value:
                report.empty_answers.append(key)
                continue
            element.fill(value)
            report.filled.append(key)

        address = page.query_selector("#address")
        if address is not None:
            composed = values.location
            if composed and values.country and values.country.lower() not in composed.lower():
                composed = f"{composed}, {values.country}"
            if not composed:
                report.empty_answers.append("address")
            else:
                address.fill(composed)
                page.wait_for_timeout(600)  # the type-ahead may offer a match
                report.filled.append("address")

        for key, selector in self.FILE.items():
            path = data.get(key, "")
            element = page.query_selector(selector)
            if element is None:
                continue
            if not path or not Path(path).is_file():
                report.empty_answers.append(key)
                continue
            element.set_input_files(path)
            time.sleep(1.5)
            report.filled.append(key)

        if ctx is not None:
            fill_questions(
                page,
                ctx,
                "input[name^='QA_'], select[name^='QA_'], textarea[name^='QA_']",
                report,
            )


class Ashby(Ats):
    """Ashby's application page, recorded from ``jobs.ashbyhq.com`` on 2026-11-24.

    Every field is addressed by ``data-field-path``. The system fields are named
    (``_systemfield_name``, ``_systemfield_email``, ``_systemfield_resume``); the
    employer's own questions are UUIDs, so they are answered by their label and
    not by a selector that would differ at the next employer.

    The first file input on the page is Ashby's *autofill* upload: sending the CV
    there makes Ashby parse it and rewrite the fields that were just filled. The
    CV goes to ``_systemfield_resume`` only.
    """

    name = "ashby"

    SYSTEM = {
        "_systemfield_name": "name",
        "_systemfield_email": "email",
        "_systemfield_phone": "phone",
        "_systemfield_linkedin": "linkedin",
    }
    SUBMIT = ".ashby-application-form-submit-button, button[type='submit']"

    def prepare(self, page) -> str:
        """A posting shows the form only after "Apply for this Job"."""
        if page.query_selector("[data-field-path]") is not None:
            return ""
        for selector in (
            "button:has-text('Apply for this Job')",
            "a:has-text('Apply for this Job')",
            "button:has-text('Apply')",
            "a:has-text('Apply')",
        ):
            control = page.query_selector(selector)
            if control is None:
                continue
            try:
                control.click(timeout=5_000)
            except Exception:  # noqa: BLE001
                continue
            page.wait_for_timeout(2_000)
            return "opened the application form"
        return ""

    def fill_form(self, page, values: Values, ctx, report: FillReport) -> None:
        data = values.as_dict()
        for entry in page.query_selector_all("[data-field-path]"):
            path = entry.get_attribute("data-field-path") or ""
            control = entry.query_selector("input, select, textarea")
            if control is None:
                continue

            if path == "_systemfield_resume":
                if not values.cv_path or not Path(values.cv_path).is_file():
                    report.empty_answers.append("resume")
                    continue
                control.set_input_files(values.cv_path)
                time.sleep(1.5)
                report.filled.append("resume")
                continue

            key = self.SYSTEM.get(path)
            if key is None:
                continue  # an employer question; the shared engine answers those
            value = data.get(key, "")
            if not value:
                report.empty_answers.append(key)
                continue
            control.fill(value)
            report.filled.append(key)

        if ctx is not None:
            fill_questions(
                page,
                ctx,
                "[data-field-path]:not([data-field-path^='_systemfield_']) input,"
                " [data-field-path]:not([data-field-path^='_systemfield_']) select,"
                " [data-field-path]:not([data-field-path^='_systemfield_']) textarea",
                report,
                report_unmatched=True,
            )


class GenericQuestions(Ats):
    """Answers whatever question fields a page shows, and nothing else.

    Used for pages whose standard fields a dedicated filler already handled —
    Workday's question step is the obvious case — and for exercising the question
    engine against a fixture.
    """

    name = "questions"

    def fill_form(self, page, values: Values, ctx, report: FillReport) -> None:
        if ctx is not None:
            fill_questions(
                page,
                ctx,
                "form input, form select, form textarea",
                report,
            )


class Workday(Ats):
    """Workday's wizard, one step at a time.

    Workday is five pages behind an account: My Information, My Experience,
    Application Questions, Voluntary Disclosures, Review. This clicks Apply,
    joins the account wall with the password from the Credential Manager, creates
    the account if asked and a verification code can be read, then fills whichever
    step is showing and — with ``advance`` — walks to the next one, stopping at
    Review. It does not submit.
    """

    name = "workday"

    # Workday puts an account wall in front of every form.
    needs_account = True

    STEP_MARKERS = (
        # Create Account and Sign In are one page, two forms, so order matters.
        ("create-account", ("[data-automation-id='createAccountSubmitButton']",)),
        (
            "sign-in",
            (
                "[data-automation-id='signInSubmitButton']",
                "[data-automation-id='signInPage']",
                "input[data-automation-id='signInEmail']",
                "#signInPage",
            ),
        ),
        # Page-level ids first. Workday keeps the previous step mounted but
        # hidden, and a hidden copy of an education year input made Voluntary
        # Disclosures look like My Experience. The page container cannot be
        # confused with another page.
        (
            "disclosures",
            (
                "[data-automation-id='applyFlowVoluntaryDisclosuresPage']",
                "[data-automation-id='voluntaryDisclosures']",
                "[data-automation-id='selfIdentify']",
            ),
        ),
        (
            "questions",
            (
                "[data-automation-id='applyFlowPrimaryQuestionsPage']",
                "[data-automation-id='applicationQuestions']",
                "[data-automation-id='questionnaire']",
            ),
        ),
        (
            "my-information",
            (
                "[data-automation-id='legalNameSection_firstName']",
                "#name--legalName--firstName",
                "input[name='legalName--firstName']",
                "#firstName",
            ),
        ),
        (
            "my-experience",
            (
                "[data-automation-id='workExperienceSection']",
                "[data-automation-id='file-upload-input-ref']",
                "[data-automation-id='select-files']",
                "[data-automation-id='dateSectionYear-input']",
                "#jobTitle",
            ),
        ),
        (
            "review",
            (
                "[data-automation-id='applyFlowReviewPage']",
                "[data-automation-id='reviewPage']",
                "[data-automation-id='submitButton']",
            ),
        ),
    )

    # Real Workday ids, both generations, with the fixture ids alongside them.
    FIELDS = {
        "first_name": (
            "[data-automation-id='legalNameSection_firstName'], "
            "#name--legalName--firstName, "
            "input[name='legalName--firstName'], #firstName"
        ),
        "last_name": (
            "[data-automation-id='legalNameSection_lastName'], "
            "#name--legalName--lastName, "
            "input[name='legalName--lastName'], #lastName"
        ),
        "email": "input[data-automation-id='email'], #email",
        "phone": (
            "input[data-automation-id='phone-number'], "
            "#phoneNumber--phoneNumber, input[name='phoneNumber'], #phone"
        ),
        "address": (
            "[data-automation-id='addressSection_addressLine1'], "
            "#address--addressLine1, input[name='addressLine1'], #address"
        ),
        "city": (
            "[data-automation-id='addressSection_city'], "
            "#address--city, input[name='city'], #city"
        ),
        "postcode": (
            "[data-automation-id='addressSection_postalCode'], "
            "#address--postalCode, input[name='postalCode'], #postalCode"
        ),
        "linkedin": "[data-automation-id='linkedin'], #linkedin",
    }
    COUNTRY = (
        "[data-automation-id='addressSection_countryRegion'], "
        "#address--countryRegion, #country"
    )
    RESUME = "input[data-automation-id='file-upload-input-ref'], input#resume, input[name='resume']"
    NEXT = "[data-automation-id='pageFooterNextButton'], button[data-action='continue']"

    # Education is a repeatable block, and Workday numbers it (education-4,
    # education-7, ...), so every selector is matched by suffix. School, Field of
    # Study, GPA and the two years are text inputs; only Degree is a dropdown.
    EDUCATION_TEXT = (
        ("education_school", "School or University", "input[id$='--schoolName']"),
        ("education_field", "Field of Study", "input[id$='--fieldOfStudy']"),
        ("education_result", "Overall Result (GPA)", "input[id$='--gradeAverage']"),
        ("education_first_year", "First year attended", "input[id$='firstYearAttended-dateSectionYear-input']"),
        ("education_last_year", "Last year attended", "input[id$='lastYearAttended-dateSectionYear-input']"),
    )
    EDUCATION_DEGREE = "button[id$='--degree']"

    def step(self, page) -> str:
        """Which step is showing. Visible only: Workday keeps hidden templates
        for other steps in the DOM, and a hidden one is not the page you are on."""
        for name, selectors in self.STEP_MARKERS:
            for selector in selectors:
                try:
                    element = page.query_selector(selector)
                    if element is not None and element.is_visible():
                        return name
                except Exception:  # noqa: BLE001
                    continue
        return ""

    def _settle(self, page, *, wait_ms: int = 1500) -> str:
        """The step showing now, or after a moment — Workday swaps them by XHR."""
        step = self.step(page)
        if step:
            return step
        page.wait_for_timeout(wait_ms)
        return self.step(page)

    def _await_step(self, page, *, seconds: float = 12.0, interval_ms: int = 500) -> str:
        """Wait for a step to render, because the swap is an XHR, not a page load.

        Between My Experience and the application questions Workday renders a
        shell of empty measuring inputs and fills it in afterwards. Checking once
        reported "unknown" and ended the run while the page was still arriving.
        """
        deadline = time.monotonic() + seconds
        while True:
            step = self.step(page)
            if step:
                return step
            if time.monotonic() >= deadline:
                return ""
            page.wait_for_timeout(interval_ms)

    def fill(
        self,
        page,
        values: Values,
        ctx=None,
        *,
        advance: bool = False,
        steps: int = 6,
        login: Login | None = None,
    ) -> FillReport:
        report = FillReport(url=page.url, ats=self.name)
        data = values.as_dict()
        email = login.email if login else ""
        password = login.password if login else ""
        create_account = login.create if login else False
        on_code = login.on_code if login else None

        # From a job posting: accept cookies, Apply, Apply Manually, then step 1.
        # A page that is already a form returns immediately.
        self.prepare(page)

        stalls = 0
        for _ in range(max(1, steps)):
            step = self._settle(page) or self._await_step(page, seconds=30.0)
            report.steps.append(step or "unknown")

            if step in ("sign-in", "create-account"):
                if not email:
                    report.error = (
                        "sign-in required: register this employer once with "
                        f'`jobhunter account add --firm "{values.org}"`'
                    )
                    return report
                if not password:
                    report.error = (
                        "no password stored for this employer: "
                        f'`jobhunter account add --firm "{values.org}"`'
                    )
                    return report
                status = self.sign_in(
                    page,
                    email=email,
                    password=password,
                    create=create_account,
                    on_code=on_code,
                )
                report.sign_in = status
                if status in ("signed-in", "created"):
                    page.wait_for_timeout(2500)
                    continue
                if status == "code-needed":
                    report.error = (
                        "verification code needed: run `jobhunter mail setup` so it can be "
                        "read from your inbox, or finish this account by hand"
                    )
                elif status == "bad-password":
                    report.error = (
                        "the stored password was rejected: refresh it with "
                        "`jobhunter account add --firm ...`"
                    )
                else:
                    report.error = status
                return report

            if step == "my-information":
                self._fields(page, data, report)
                self._extras(page, ctx, report)
            elif step == "my-experience":
                self._resume(page, data, report)
                self._education(page, ctx, report)
            elif step == "questions":
                # The questions page uses the same dropdown and radio widgets as
                # My Information; only the question text lives in a label rather
                # than in the control's aria-label.
                self._extras(page, ctx, report)
                if ctx:
                    # Only free-text here: the dropdowns and radios are the
                    # widget filler's job, and running the generic engine over
                    # them produced eleven "could not fill" lines for fields it
                    # had just filled.
                    fill_questions(page, ctx, "form textarea", report)
            elif step == "disclosures":
                report.questions_review.append(
                    ("Voluntary disclosures", "protected: never auto-answered")
                )
                # Stop here rather than clicking Next into a wall of required
                # protected fields. This step is the human's, by design.
                report.dedupe()
                return report
            else:
                # Review, or a page this filler does not know: stop rather than
                # click through something it cannot read.
                return report

            if not advance:
                return report

            button = page.query_selector(self.NEXT)
            if button is None:
                return report
            _click(page, button)
            page.wait_for_timeout(2500)

            # Six Next clicks into the same validation wall is worse than one:
            # it looks like a broken tool and it teaches us nothing. If the step
            # did not change after a second press, stop and say which fields the
            # page is complaining about.
            if self._settle(page) == step:
                stalls += 1
                if stalls >= 2:
                    invalid = self._invalid_fields(page)
                    report.error = (
                        f"the form will not advance from {step}: "
                        + (", ".join(invalid) if invalid else "the page gives no reason")
                    )
                    return report
            else:
                stalls = 0

        report.dedupe()
        return report

    @staticmethod
    def _invalid_fields(page) -> list[str]:
        """Labels of the fields Workday has marked invalid, if any."""
        try:
            labels = page.evaluate(
                """
                () => {
                  const out = [];
                  for (const el of document.querySelectorAll("[aria-invalid='true']")) {
                    const label = (el.labels && el.labels.length ? el.labels[0].innerText : "")
                      || el.getAttribute("name") || el.id || el.tagName;
                    out.push(label.replace(/\\s*\\*\\s*$/, "").trim());
                  }
                  return out.slice(0, 8);
                }
                """
            )
            return [str(label) for label in labels]
        except Exception:  # noqa: BLE001
            return []

    # --- Workday's custom widgets -----------------------------------------
    #
    # A dropdown here is not a <select>: it is a
    # <button aria-haspopup="listbox"> that opens a role=listbox popup. A radio
    # group's question text lives in a div's aria-labelledby, and its options are
    # labelled by id, not by a <label for>. Filling either with .fill() does
    # nothing at all, which is how My Information stalled on four required
    # controls while every text field was already correct.

    @staticmethod
    def _clean_label(text: str) -> str:
        cleaned = " ".join((text or "").split())
        for suffix in (" Required", " Select One", " Select One Required"):
            while cleaned.lower().endswith(suffix.strip().lower()):
                cleaned = cleaned[: -len(suffix.strip())].strip()
        return cleaned

    @classmethod
    def _dropdown_buttons(cls, page):
        """The unset Workday dropdowns: no value yet, and not a nav menu."""
        found = []
        for button in page.query_selector_all("button[aria-haspopup='listbox']"):
            try:
                if not button.is_visible():
                    continue
                if (button.get_attribute("value") or "").strip():
                    continue
                if (button.get_attribute("data-automation-id") or "") == "utilityMenuButton":
                    continue
                if not (button.get_attribute("aria-label") or "").strip():
                    continue
                found.append(button)
            except Exception:  # noqa: BLE001
                continue
        return found

    @classmethod
    def _option(cls, page, wanted: str):
        """The visible popup option whose text is exactly ``wanted``, or None."""
        target = " ".join((wanted or "").split()).lower()
        if not target:
            return None
        for option in cls._visible_options(page):
            if cls._option_text(option) == target:
                return option
        return None

    @staticmethod
    def _option_text(option) -> str:
        try:
            return " ".join((option.inner_text() or "").split()).lower()
        except Exception:  # noqa: BLE001
            return ""

    @classmethod
    def _visible_options(cls, page):
        """Every choice the open popup is offering, in document order.

        Scoped to the open listbox when there is one: the phone country picker
        renders a `promptOption` of its own, and an unrelated "United Kingdom
        (+44)" in the source list would be noise the resolver could match.
        """
        candidates = []
        container = cls._option_container(page)
        sources = [container] if container is not None else [page]
        for scope in sources:
            for selector in ("[role='option']", "[data-automation-id='promptOption']"):
                try:
                    candidates.extend(scope.query_selector_all(selector))
                except Exception:  # noqa: BLE001
                    continue
        visible = []
        for option in candidates:
            try:
                if not option.is_visible():
                    continue
                # The selected-item pill is also role=option; it is not a choice.
                if (option.get_attribute("data-automation-id") or "") == "selectedItem":
                    continue
                # "Select One" is the placeholder, and is disabled on purpose.
                if (option.get_attribute("aria-disabled") or "").lower() == "true":
                    continue
                if cls._option_text(option) in ("", "select one"):
                    continue
                visible.append(option)
            except Exception:  # noqa: BLE001
                continue
        return visible

    @classmethod
    def _option_texts(cls, page) -> list[str]:
        seen: list[str] = []
        for option in cls._visible_options(page):
            text = cls._option_text(option)
            if text and text not in seen:
                seen.append(text)
        return seen

    def _dropdown(
        self,
        page,
        button,
        question: str,
        answer: str,
        *,
        tenant: str = "",
        report=None,
    ) -> bool:
        """Open a Workday dropdown and take the option the resolver picks.

        The resolver only ever returns a string the popup actually offered, so
        nothing here can invent a value: it can choose badly, and that is
        reported, but it cannot type.
        """
        from . import choices

        _click(page, button)
        page.wait_for_timeout(800)
        options = self._all_option_texts(page)
        picked = choices.choose(question, options, answer, tenant=tenant) if options else None
        if picked is None:
            # Leaving the popup open would cover the next field.
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(300)
            return False

        option_text, reason = picked
        element = self._option_by_text(page, option_text)
        if element is None:
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(300)
            return False

        _click(page, element)
        page.wait_for_timeout(600)
        if reason != "exact" and report is not None:
            report.notes.append(
                f"{question}: chose {option_text!r} ({reason}); the bank says {answer!r}"
            )
            if tenant:
                choices.remember(tenant, question, option_text)
        return True

    # --- a list too long to render at once -------------------------------
    #
    # "How Did You Hear About Us?" at M&G offers around a hundred sources, and
    # Workday renders roughly the visible screenful. "Company Website" is not in
    # the DOM until you scroll to it, so text matching over the current options
    # finds nothing. Collect by scrolling, then scroll back to the choice.

    @classmethod
    def _option_container(cls, page):
        for selector in (
            "ul[role='listbox']:not([data-automation-id='selectedItemList'])",
            "[role='listbox']:not([data-automation-id='selectedItemList'])",
        ):
            try:
                for element in page.query_selector_all(selector):
                    if not element.is_visible():
                        continue
                    if element.query_selector("[role='option']") is not None:
                        return element
            except Exception:  # noqa: BLE001
                continue
        return None

    @staticmethod
    def _scroll_options(page, container, *, to_top: bool = False) -> bool:
        """Scroll the open list by a screenful. False when it is already at the end."""
        try:
            return bool(
                page.evaluate(
                    """([el, toTop]) => {
                      if (toTop) { el.scrollTop = 0; return true; }
                      const before = el.scrollTop;
                      el.scrollTop = Math.min(
                        el.scrollTop + Math.max(el.clientHeight * 0.8, 120),
                        el.scrollHeight);
                      return el.scrollTop !== before;
                    }""",
                    [container, to_top],
                )
            )
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    def _all_option_texts(cls, page, limit: int = 80) -> list[str]:
        container = cls._option_container(page)
        texts: list[str] = []
        seen: set[str] = set()
        for _ in range(limit):
            for option in cls._visible_options(page):
                text = cls._option_text(option)
                if text and text not in seen:
                    seen.add(text)
                    texts.append(text)
            if container is None or not cls._scroll_options(page, container):
                break
            page.wait_for_timeout(150)
        return texts

    @classmethod
    def _option_by_text(cls, page, wanted: str):
        """Find an option by text, scrolling the virtualised list to reach it."""
        target = " ".join((wanted or "").split()).lower()
        container = cls._option_container(page)
        if container is not None:
            cls._scroll_options(page, container, to_top=True)
            page.wait_for_timeout(150)
        for _ in range(80):
            for option in cls._visible_options(page):
                if cls._option_text(option) == target:
                    return option
            if container is None or not cls._scroll_options(page, container):
                return None
            page.wait_for_timeout(150)
        return None

    @staticmethod
    def _group_question(page, name: str) -> str:
        try:
            return (
                page.evaluate(
                    """
                    (name) => {
                      const input = document.querySelector(`input[type=radio][name="${name}"]`);
                      if (!input) return "";
                      const group = input.closest("[role=group]") || input.closest("fieldset");
                      const text = (el) => (el ? (el.innerText || "").trim() : "");
                      if (group) {
                        const id = group.getAttribute("aria-labelledby");
                        if (id) {
                          const labelled = document.getElementById(id);
                          if (labelled && text(labelled)) return text(labelled);
                        }
                        const legend = group.querySelector("legend");
                        if (text(legend)) return text(legend);
                      }
                      return "";
                    }
                    """,
                    name,
                )
                or ""
            )
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _radio_index(page, name: str, wanted: str) -> int:
        """Index of the radio in this group whose option text is wanted, or -1."""
        try:
            return int(
                page.evaluate(
                    """
                    ([name, wanted]) => {
                      const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();
                      const labelOf = (input) => {
                        // A radio's option text is usually a sibling
                        // <label for="id">, not an ancestor and not aria-labelledby.
                        const labels = input.labels ? Array.from(input.labels) : [];
                        const text = labels.map((el) => el.innerText || "").join(" ");
                        if (text.trim()) return text;
                        const ids = (input.getAttribute("aria-labelledby") || "")
                          .split(/\\s+/).filter(Boolean);
                        for (const id of ids) {
                          const el = document.getElementById(id);
                          if (el && (el.innerText || "").trim()) return el.innerText;
                        }
                        const ancestor = input.closest("label");
                        return ancestor ? ancestor.innerText : "";
                      };
                      const radios = Array.from(
                        document.querySelectorAll(`input[type=radio][name="${name}"]`));
                      for (let index = 0; index < radios.length; index += 1) {
                        const input = radios[index];
                        if (norm(labelOf(input)) === norm(wanted)) return index;
                      }
                      return -1;
                    }
                    """,
                    [name, wanted],
                )
            )
        except Exception:  # noqa: BLE001
            return -1

    def _select_radio(self, page, name: str, wanted: str) -> bool:
        """Choose a radio option by its text, the way the widget expects.

        At M&G the input is `opacity: 0` and its option text is a sibling
        `<label for="...">`, so the label is the surface a person clicks. The
        input is checked too, because some tenants style it differently.
        """
        index = self._radio_index(page, name, wanted)
        if index < 0:
            return False
        try:
            radios = page.query_selector_all(f"input[type=radio][name='{name}']")
        except Exception:  # noqa: BLE001
            return False
        if index >= len(radios):
            return False
        radio = radios[index]

        # 1. The label a person actually clicks.
        try:
            radio_id = radio.get_attribute("id") or ""
            if radio_id:
                surface = page.query_selector(f"label[for='{radio_id}']")
                if surface is not None and _click(page, surface):
                    if radio.is_checked():
                        return True
        except Exception:  # noqa: BLE001
            pass

        # 2. The input itself.
        try:
            radio.check(timeout=2500)
            return True
        except Exception:  # noqa: BLE001
            pass
        return _click(page, radio)

    @staticmethod
    def _question_text(button) -> str:
        """The words above a dropdown, which Workday keeps out of aria-label.

        On Application Questions every aria-label is just "Select One Required";
        the question is in a label or in the field container.
        """
        try:
            text = button.evaluate(
                """
                (el) => {
                  const clean = (s) => (s || "").replace(/\\s+/g, " ").trim();
                  const ids = (el.getAttribute("aria-labelledby") || "")
                    .split(/\\s+/).filter(Boolean);
                  for (const id of ids) {
                    const node = document.getElementById(id);
                    if (node && clean(node.innerText)) return clean(node.innerText);
                  }
                  const field = el.closest("[data-automation-id^='formField-']");
                  if (field) {
                    const label = field.querySelector("label");
                    if (label && clean(label.innerText)) return clean(label.innerText);
                    const whole = clean(field.innerText);
                    if (whole) return whole;
                  }
                  return "";
                }
                """
            )
        except Exception:  # noqa: BLE001
            return ""
        return str(text or "")

    def _extras(self, page, ctx, report) -> None:
        """Answer the dropdowns and radio groups on My Information."""
        from . import choices, questions

        if ctx is None:
            return

        tenant = ""
        try:
            from . import accounts as accounts_module

            tenant = accounts_module.tenant_from_url(page.url)
        except Exception:  # noqa: BLE001
            tenant = ""
        tenant = tenant or str(getattr(ctx, "company", "") or "")

        for button in self._dropdown_buttons(page):
            try:
                label = self._clean_label(self._question_text(button)) or self._clean_label(
                    button.get_attribute("aria-label") or ""
                )
                answer = questions.answer_for(label, ctx)
                if answer is None or not answer.value:
                    report.empty_answers.append(label or "a dropdown")
                    continue
                if self._dropdown(
                    page,
                    button,
                    label,
                    answer.value,
                    tenant=tenant,
                    report=report,
                ):
                    report.filled.append(label)
                else:
                    report.empty_answers.append(f"{label} (no option for {answer.value!r})")
            except Exception:  # noqa: BLE001
                report.empty_answers.append("a dropdown that would not open")

        try:
            names = page.evaluate(
                """
                () => Array.from(new Set(
                  Array.from(document.querySelectorAll("input[type=radio]"))
                    .filter(el => el.name && el.offsetParent !== null)
                    .map(el => el.name)))
                """
            )
        except Exception:  # noqa: BLE001
            return

        for name in names or []:
            question = self._group_question(page, name) or name
            answer = questions.answer_for(question, ctx)
            if answer is None or not answer.value:
                report.empty_answers.append(question)
                continue
            choice = self._select_radio(page, name, answer.value)
            if not choice:
                report.empty_answers.append(f"{question} (no option for {answer.value!r})")
                continue
            report.filled.append(question)

    def _fields(self, page, data, report):
        for key, selector in self.FIELDS.items():
            element = self._editable(page, (selector,))
            if element is None:
                continue
            value = data.get(key, "")
            if not value:
                report.empty_answers.append(key)
                continue
            # _type, not fill: these are React controlled inputs, and a fill that
            # "looks" applied can leave the component's state empty.
            _type(element, value)
            report.filled.append(key)

        country = self._one(page, self.COUNTRY)
        if country is not None:
            value = data.get("country", "")
            if not value:
                report.empty_answers.append("country")
            else:
                try:
                    _select_by_text(country, value)
                    report.filled.append("country")
                except Exception:  # noqa: BLE001
                    report.skipped.append(f"country (no option matching {value!r})")

    def _resume(self, page, data, report):
        # A run that resumed a draft found the CV already attached; uploading
        # again leaves a second copy, and the previous runs had left five.
        if page.query_selector("[data-automation-id='delete-file']") is not None:
            report.filled.append("resume (already attached)")
            return
        element = self._one(page, self.RESUME)
        if element is None:
            return
        path = data.get("resume", "")
        if not path or not Path(path).is_file():
            report.empty_answers.append("resume")
            return
        element.set_input_files(path)
        # Workday parses the CV and fills the history with it; the parser is
        # decent and wrong in places, which is why the review step exists. On the
        # live tenant the parse takes longer than a page transition does.
        page.wait_for_timeout(8000)
        report.filled.append("resume")

    @staticmethod
    def _one(page, selector):
        try:
            return page.query_selector(selector)
        except Exception:  # noqa: BLE001
            return None

    def _education(self, page, ctx, report) -> None:
        """Fill the education block from the answer bank.

        School and Field of Study look like plain inputs and behave like them,
        but Workday sometimes attaches a suggestion list; typing the value and
        taking a matching suggestion when one appears covers both. Degree is a
        real dropdown, matched through the resolver with degree synonyms, so a
        bank saying "MSc" can take "Master of Science (MSc)".
        """
        from . import choices, questions

        if ctx is None:
            return

        tenant = ""
        try:
            from . import accounts as accounts_module

            tenant = accounts_module.tenant_from_url(page.url)
        except Exception:  # noqa: BLE001
            tenant = ""

        for key, question, selector in self.EDUCATION_TEXT:
            value = str(ctx.get(key, "") or "")
            element = self._editable(page, (selector,))
            if element is None:
                continue
            if not value:
                report.empty_answers.append(question)
                continue
            try:
                self._type_or_select(page, element, value, question, tenant=tenant, report=report)
                report.filled.append(question)
            except Exception:  # noqa: BLE001
                report.empty_answers.append(question)

        value = str(ctx.get("education_degree", "") or "")
        button = self._clickable(page, (self.EDUCATION_DEGREE,))
        if button is None:
            return
        if not value:
            report.empty_answers.append("Degree")
            return
        if self._dropdown(page, button, "Degree", value, tenant=tenant, report=report):
            report.filled.append("Degree")
        else:
            report.empty_answers.append(f"Degree (no option for {value!r})")

    def _type_or_select(self, page, element, value, question, *, tenant="", report=None) -> bool:
        """Type a value, and take a suggestion if the field offers one."""
        from . import choices

        _click(page, element)
        try:
            element.fill("")
        except Exception:  # noqa: BLE001
            pass
        _type(element, value)
        page.wait_for_timeout(1500)

        options = self._all_option_texts(page)
        if not options:
            return True
        picked = choices.choose(question, options, value, tenant=tenant)
        if picked is None:
            return True
        suggestion = self._option_by_text(page, picked[0])
        if suggestion is None:
            return True
        _click(page, suggestion)
        page.wait_for_timeout(500)
        if picked[1] != "exact" and report is not None:
            report.notes.append(
                f"{question}: typed {value!r}, took {picked[0]!r} ({picked[1]})"
            )
        return True

    # --- getting through the account wall ---------------------------------
    #
    # Written against the live M&G tenant, which is a Workday 2024 release:
    # Apply is an <a> that opens a shelf (Autofill with Resume / Apply Manually /
    # Use My Last Application), Apply Manually lands on step 1 of 6 — Create
    # Account — and "Sign In" is a button on that same form, not a separate page.
    #
    # Every selector is a list because ids drift between tenants and releases.

    COOKIES = (
        "[data-automation-id='legalNoticeAcceptButton']",
        "[data-automation-id='legalNoticeDeclineButton']",
    )
    # Once a draft exists, the job page offers Continue instead of Apply.
    CONTINUE = (
        "a[data-automation-id='continueButton']",
        "[data-automation-id='continueButton']",
    )
    # Whichever route the shelf offers: manual first, because Autofill parses the
    # CV into work history and those guesses are harder to check than an empty
    # form, and "Use My Last Application" is closest to a filled one.
    SHELF_ROUTES = (
        "[data-automation-id='applyManually']",
        "[data-automation-id='autofillWithResume']",
        "[data-automation-id='useMyLastApplication']",
    )
    APPLY = (
        "a[data-automation-id='adventureButton']",
        "[data-automation-id='adventureButton']",
        "button[data-automation-id='applyButton']",
    )
    APPLY_MANUALLY = (
        "a[data-automation-id='applyManually']",
        "[data-automation-id='applyManually']",
    )

    CREATE = {
        "email": ("input[data-automation-id='email']", "#email"),
        "password": ("input[data-automation-id='password']", "#password"),
        "verify": ("input[data-automation-id='verifyPassword']", "#verifyPassword"),
        "terms": ("input[data-automation-id='createAccountCheckbox']",),
        "submit": (
            "button[data-automation-id='createAccountSubmitButton']",
            "[data-automation-id='createAccountSubmitButton']",
        ),
    }
    SIGNIN = {
        "email": ("input[data-automation-id='email']", "#email"),
        "password": ("input[data-automation-id='password']", "#password"),
        "submit": (
            "button[data-automation-id='signInSubmitButton']",
            "[data-automation-id='signInSubmitButton']",
            "[data-automation-id='signInButton']",
        ),
    }
    # The two forms are one page with a toggle between them.
    TO_SIGNIN = ("[data-automation-id='signInLink']",)
    TO_CREATE = (
        "button[data-automation-id='createAccountLink']",
        "[data-automation-id='createAccountLink']",
    )
    CODE = (
        "input[data-automation-id='verificationCode']",
        "input[data-automation-id='verifyCode']",
        "input[autocomplete='one-time-code']",
        "#verificationCode",
        "input[name='verificationCode']",
    )
    ERROR = (
        "[data-automation-id='errorMessage']",
        "[data-automation-id='inputAlert']",
        "[data-automation-id*='rror']",
        "[role='alert']",
    )

    # A field that exists only to catch bots: one pixel tall, named "website",
    # labelled "for robots only". Filling it flags the application, so every
    # path that writes to a field has to refuse it.
    HONEYPOT_IDS = ("beecatcher",)

    def prepare(self, page, *, timeout_ms: int = 45_000) -> str:
        """Get from the job posting to the first form, and report which one.

        Returns the step name, or '' if the page never became a form. Accepts the
        cookie banner on the way, because it deliberately covers the Apply
        button's hit area.
        """
        step = self.step(page)
        if step:
            return step

        self._dismiss_cookies(page)

        # A draft that already exists is reached through Continue, not Apply.
        resume = self._await(page, self.CONTINUE, 8_000)
        if resume is not None:
            _click(page, resume)
            page.wait_for_timeout(3000)
            reached = self._settle(page, wait_ms=3000)
            if reached:
                return reached

        apply_button = self._await(page, self.APPLY, timeout_ms)
        if apply_button is not None:
            _click(page, apply_button)
            page.wait_for_timeout(1500)

        # The shelf: prefer the manual route.
        manual = self._await(page, self.APPLY_MANUALLY, 20_000)
        if manual is None:
            manual = self._await(page, self.SHELF_ROUTES, 6_000)
        if manual is not None:
            _click(page, manual)
            page.wait_for_timeout(2500)

        return self._settle(page, wait_ms=3000)

    def start(self, page, **kwargs) -> str:
        """Kept for callers that predate the name; it prepares the first form."""
        return self.prepare(page, **kwargs)

    def sign_in(
        self,
        page,
        *,
        email: str,
        password: str,
        create: bool = False,
        verification: str = "",
        on_code=None,
        wait_ms: int = 2500,
    ) -> str:
        """Join the account wall, or climb it.

        Returns ``signed-in``, ``created``, ``code-needed``, ``bad-password`` or
        ``failed: <what the page said>``. It only creates an account when asked:
        a stray account at an employer is a dead login to explain later.
        """
        if not email or not password:
            return "failed: no stored account for this employer"

        step = self._settle(page)

        # Both forms can be on the page at once with one hidden behind a toggle,
        # so the test is "is the submit visible", not "which step name is this".
        if create:
            if self._clickable(page, self.CREATE["submit"]) is None:
                toggle = self._clickable(page, self.TO_CREATE)
                if toggle is None:
                    return "failed: no Create Account form on this page"
                _click(page, toggle)
                page.wait_for_timeout(wait_ms)
            fields = self.CREATE
            outcome = "created"
        else:
            if step != "sign-in" and self._clickable(page, self.SIGNIN["submit"]) is None:
                toggle = self._clickable(page, self.TO_SIGNIN)
                if toggle is None:
                    return "failed: no Sign In form on this page"
                _click(page, toggle)
                page.wait_for_timeout(wait_ms)
            fields = self.SIGNIN
            outcome = "signed-in"

        email_field = self._editable(page, fields["email"])
        password_field = self._editable(page, fields["password"])
        if email_field is None or password_field is None:
            return "failed: the account form has no email or password field"
        _type(email_field, email)
        _type(password_field, password)

        if create:
            confirm = self._editable(page, self.CREATE["verify"])
            if confirm is not None:
                _type(confirm, password)
            terms = self._clickable(page, self.CREATE["terms"])
            if terms is not None:
                try:
                    if not terms.is_checked():
                        _click(page, terms)
                except Exception:  # noqa: BLE001
                    pass

        submit = self._clickable(page, fields["submit"])
        if submit is None:
            return "failed: no submit button on the account form"
        self._submit_until_it_moves(page, submit, password_field, wait_ms=wait_ms)

        code_field = self._code_field(page)
        if code_field is not None:
            code = ""
            if on_code is not None:
                code = str(on_code() or "")
            elif verification:
                code = verification
            if not code:
                return "code-needed"
            _type(code_field, code)
            submit = self._clickable(page, self.CREATE["submit"]) or self._clickable(
                page, self.SIGNIN["submit"]
            )
            if submit is None:
                submit = self._clickable(page, ("[data-automation-id='verifyButton']",))
            if submit is not None:
                self._submit_until_it_moves(page, submit, code_field, wait_ms=wait_ms)

        if self._settle(page) in ("create-account", "sign-in"):
            complaint = self._text_of(page, self.ERROR)
            lowered = complaint.lower()
            if "incorrect" in lowered or "invalid" in lowered or "password" in lowered:
                return "bad-password"
            if "already" in lowered and "account" in lowered:
                return "failed: an account already exists for this email — sign in instead"
            return f"failed: {complaint or 'still on the account form'}"

        # Some tenants do not ask for a code at all: they send a link. Either
        # way the account is not usable until a human has been to their inbox,
        # so say so instead of claiming success.
        if self._page_says(page, ("check your email", "verify your email", "we have sent", "we sent")):
            return "code-needed"
        return outcome

    @staticmethod
    def _page_says(page, phrases) -> bool:
        try:
            text = (page.inner_text("body") or "").lower()
        except Exception:  # noqa: BLE001
            return False
        return any(phrase in text for phrase in phrases)

    # --- selector plumbing ------------------------------------------------

    def _submit_until_it_moves(self, page, button, password_field, *, wait_ms: int = 2500) -> None:
        """Press submit, and check the page actually did something.

        Measured on the live tenant: a click can be reported as delivered while
        the form sits there untouched, password field still focused and no error
        shown. So: click, watch for progress; if nothing moved, press Enter in
        the password field — which is what a person would do, and which no
        overlay can intercept; then click once more.
        """
        _click(page, button)
        if self._wait_for_progress(page, 4.0):
            return

        try:
            password_field.press("Enter")
        except Exception:  # noqa: BLE001
            pass
        if self._wait_for_progress(page, 4.0):
            return

        _click(page, button)
        page.wait_for_timeout(wait_ms)

    def _progressed(self, page) -> bool:
        """Has the account form been left behind, or has it complained?"""
        if self.step(page) not in ("create-account", "sign-in"):
            return True
        return bool(self._text_of(page, self.ERROR))

    def _wait_for_progress(self, page, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._progressed(page):
                return True
            page.wait_for_timeout(400)
        return self._progressed(page)

    def _dismiss_cookies(self, page) -> None:
        for selector in self.COOKIES:
            try:
                button = page.query_selector(selector)
                if button is not None and button.is_visible():
                    _click(page, button, timeout_ms=4000)
                    page.wait_for_timeout(500)
                    return
            except Exception:  # noqa: BLE001
                continue

    @staticmethod
    def _await(page, selectors, timeout_ms: int):
        """First of these that becomes visible, sharing the budget between them."""
        remaining = timeout_ms
        for selector in selectors:
            if remaining <= 0:
                return None
            try:
                return page.wait_for_selector(selector, timeout=remaining, state="visible")
            except Exception:  # noqa: BLE001
                remaining = max(1000, remaining // 2)
        return None

    def _code_field(self, page):
        for selector in self.CODE:
            try:
                for element in page.query_selector_all(selector):
                    if element.is_visible() and not self._is_honeypot(element):
                        return element
            except Exception:  # noqa: BLE001
                continue
        return None

    @staticmethod
    def _is_honeypot(element) -> bool:
        """Is this Workday's bot trap, rather than a field a person can fill?

        Identity only, never size. An earlier version also refused anything
        smaller than 2px, which sounded prudent until it hit the year spinners on
        My Experience: real fields there are 1px inputs sitting behind a styled
        "YYYY" label, and refusing them left two required fields permanently
        blank.
        """
        try:
            if element.get_attribute("data-automation-id") in Workday.HONEYPOT_IDS:
                return True
            name = (element.get_attribute("name") or "").strip().lower()
            if name == "website":
                return True
            label = (element.get_attribute("aria-label") or "").lower()
            if "robots only" in label or "do not enter" in label:
                return True
        except Exception:  # noqa: BLE001
            return True
        return False

    @staticmethod
    def _first(page, selectors) -> object | None:
        for selector in selectors:
            try:
                element = page.query_selector(selector)
            except Exception:  # noqa: BLE001
                continue
            if element is not None:
                return element
        return None

    @classmethod
    def _editable(cls, page, selectors):
        for selector in selectors:
            try:
                for element in page.query_selector_all(selector):
                    if not element.is_visible() or not element.is_enabled():
                        continue
                    if cls._is_honeypot(element):
                        continue
                    return element
            except Exception:  # noqa: BLE001
                continue
        return None

    @classmethod
    def _clickable(cls, page, selectors):
        for selector in selectors:
            try:
                for element in page.query_selector_all(selector):
                    if element.is_visible() and not cls._is_honeypot(element):
                        return element
            except Exception:  # noqa: BLE001
                continue
        return None

    @staticmethod
    def _text_of(page, selectors) -> str:
        for selector in selectors:
            try:
                element = page.query_selector(selector)
                if element is not None:
                    text = (element.inner_text() or "").strip()
                    if text:
                        return " ".join(text.split())[:200]
            except Exception:  # noqa: BLE001
                continue
        return ""


def _click(page, element, *, timeout_ms: int = 4000) -> bool:
    """Click what a person would click, even when an overlay owns the handler.

    Workday hides the real ``<button>`` (``aria-hidden="true"``, ``tabindex="-2"``)
    and puts a ``<div role="button" data-automation-id="click_filter">`` in front
    of it. That div carries the handler; the button is a decoy. Playwright
    refuses to click an element another element covers — correct in general, and
    useless here — and a synthetic ``el.click()`` on the decoy does nothing at
    all, silently. An earlier version "solved" this by deleting the overlay,
    which deleted the only thing that could submit the form.

    So: try the real click; if something covers it, click at the element's own
    coordinates and let the page decide who receives it. That is what a human
    click does, and the overlay is designed to receive it.
    """
    try:
        element.click(timeout=timeout_ms)
        return True
    except Exception:  # noqa: BLE001
        pass

    try:
        element.scroll_into_view_if_needed(timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        pass
    try:
        box = element.bounding_box()
        if box is None:
            return False
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        page.mouse.click(x, y)
        return True
    except Exception:  # noqa: BLE001
        return False


def _type(element, value: str) -> None:
    """Put text in a field the way a person does, and check that it stuck.

    Workday's inputs are React controlled components: a bare value assignment is
    often ignored, and so is the input event that ``fill`` dispatches. The field
    can look filled on screen while the component's state is still empty, and
    the form then refuses to submit with no error at all — which is exactly how
    one live run died, password field still focused and nothing said.

    So: click, fill, dispatch change, then read the value back. If it did not
    stick, clear the field and send real keystrokes, which no controlled
    component can ignore.
    """
    if (element.get_attribute("data-automation-id") or "") in Workday.HONEYPOT_IDS:
        raise ValueError("refusing to type into Workday's bot-catcher field")
    if value == "":
        try:
            element.fill("")
        except Exception:  # noqa: BLE001
            pass
        return

    for _ in range(2):
        try:
            element.click(timeout=3000)
        except Exception:  # noqa: BLE001
            pass
        try:
            element.fill(value)
            element.dispatch_event("change")
        except Exception:  # noqa: BLE001
            pass
        current = _value_of(element)
        if current is None or current == value:
            return

        # The field kept an older value: send keystrokes instead.
        try:
            element.click(timeout=3000)
            element.press("Control+a")
            element.press("Delete")
            element.type(value, delay=15)
        except Exception:  # noqa: BLE001
            pass
        current = _value_of(element)
        if current is None or current == value:
            return

        try:
            element.fill(value)
        except Exception:  # noqa: BLE001
            pass
        current = _value_of(element)
        if current is None or current == value:
            return


def _value_of(element) -> str | None:
    """What a field actually holds, or None if that cannot be read.

    None means "no way to verify", which callers treat as success: looping
    forever on a field that cannot be inspected would be worse than trusting it.
    """
    try:
        return element.input_value()
    except Exception:  # noqa: BLE001
        return None


FILLERS = {
    "greenhouse": Greenhouse,
    "lever": Lever,
    "workable": Workable,
    "ashby": Ashby,
    "workday": Workday,
    "questions": GenericQuestions,
}


def fill_questions(page, ctx, selector: str, report: FillReport, *, report_unmatched: bool = False) -> None:
    """Answer whichever custom questions the page shows, from the answer bank.

    Works on any markup: it reads the question from the label or the fieldset
    legend, so the same code answers a Greenhouse ``#question_*`` field, a Lever
    ``cards[...]`` field and a Workday fieldset.

    ``report_unmatched`` lists questions no rule claims. Some pages mark their
    required fields in the markup and can be left to report themselves; others
    (Ashby) mark them only with a class, so the only way the human learns what is
    still blank is to be told.
    """
    from . import questions

    seen: set[str] = set()
    for element in page.query_selector_all(selector):
        name = element.get_attribute("name") or element.get_attribute("id") or ""
        widget = questions.widget_of(element)
        if widget in ("hidden", "submit", "button", "file"):
            continue
        # Workday plants a one-pixel field called "website" to catch bots. Its
        # aria-label says so; filling it is the one mistake that loses the whole
        # application, so nothing here ever writes to it.
        if element.get_attribute("data-automation-id") in Workday.HONEYPOT_IDS:
            continue
        if "robots only" in (element.get_attribute("aria-label") or "").lower():
            continue
        # One pass per radio group, keyed by name.
        if widget == "radio":
            if name in seen:
                continue
            seen.add(name)

        label = questions.question_text(element)
        if _is_date_field(element):
            # A date is not a sentence: "Available immediately" in a date box is
            # a failed validation, and a guessed date is a claim about him.
            report.questions_review.append(
                (label or name, "date field: needs a real date, fill it yourself")
            )
            continue
        answer = questions.answer_for(label, ctx)
        if answer is None:
            # Not a question this engine claims to answer.
            if report_unmatched:
                report.questions_review.append(
                    (label or name, "no answer rule for this question")
                )
            continue
        if answer.confidence != "certain" or not answer.value:
            report.questions_review.append((label, answer.reason))
            continue

        try:
            if widget == "select":
                _select_by_text(element, answer.value)
            elif widget in ("text", "search") and (element.get_attribute("role") or "").lower() == "combobox":
                chosen = _combo(page, element, answer.value)
                if chosen and answer.value.lower() in chosen.lower():
                    report.questions_answered.append((label, chosen))
                else:
                    report.questions_review.append(
                        (label, f"no '{answer.value}' option (chose {chosen or 'nothing'})")
                    )
                continue
            elif widget == "checkbox" and _is_single_yes_no(page, element):
                _tick(page, element, answer.value == "Yes")
            elif widget in ("radio", "checkbox"):
                if not _click_option(page, element, answer.value):
                    report.questions_review.append((label, f"no '{answer.value}' option on the page"))
                    continue
            else:
                element.fill(answer.value)
            report.questions_answered.append((label, answer.value))
        except Exception as error:  # noqa: BLE001
            reason = str(error) if isinstance(error, LookupError) else f"could not fill: {type(error).__name__}"
            report.questions_review.append((label, reason))


def _is_single_yes_no(page, element) -> bool:
    """One checkbox standing in for a Yes/No answer.

    Ashby renders "Will you require sponsorship?" as a lone checkbox: ticked
    means Yes. Clicking at an option named "No" is meaningless there, so the
    answer becomes the box's checked state.
    """
    name = element.get_attribute("name") or element.get_attribute("id") or ""
    if not name:
        return False
    try:
        group = page.query_selector_all(f"input[name='{name}']")
    except Exception:  # noqa: BLE001
        return False
    return len(group) == 1


def _tick(page, element, checked: bool) -> None:
    """Set a checkbox, clicking the visible label when the input itself is hidden."""
    if element.is_checked() == checked:
        return
    try:
        element.check(timeout=2_000) if checked else element.uncheck(timeout=2_000)
        return
    except Exception:  # noqa: BLE001
        pass
    _press_option(element)


def _is_date_field(element) -> bool:
    """A date picker, however the site dresses it up as a text box."""
    try:
        kind = (element.get_attribute("type") or "").lower()
        classes = (element.get_attribute("class") or "").lower()
        placeholder = (element.get_attribute("placeholder") or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return (
        kind == "date"
        or "datepicker" in classes
        or "input-date" in classes
        or placeholder.startswith("pick date")
    )


def pick_option(offered: list[str], value: str) -> str:
    """The label to choose from a dropdown's own options, or ''.

    Exact first, then prefix: "United Kingdom" must not pick "United Kingdom
    Overseas Territories" when both are offered, but it must pick "United
    Kingdom +44" when the board decorates the label with a dial code.

    Matching is on the *option text*, never on the typed string, so a miss
    cannot pass for a hit (the earlier version compared "No" against the text
    "no match for 'No'" and said yes).
    """
    wanted = (value or "").strip().lower()
    if not wanted:
        return ""
    for option in offered:
        if option.strip().lower() == wanted:
            return option
    for option in offered:
        if option.strip().lower().startswith(wanted):
            return option
    return ""


def _combo(page, element, value: str) -> str:
    """Choose from a searchable dropdown that is not a ``<select>``.

    Greenhouse's current form renders its country field and every dropdown
    question as a react-select: an ``input[role=combobox]`` with a menu. Typing
    filters it, Enter takes the highlighted option, and the chosen label is read
    back from the control — so a miss is reported rather than assumed.
    """
    try:
        element.scroll_into_view_if_needed(timeout=2_000)
    except Exception:  # noqa: BLE001
        pass
    element.click()
    try:
        element.fill("")
    except Exception:  # noqa: BLE001
        pass
    element.type(str(value), delay=25)
    page.wait_for_timeout(700)

    # Choose the option rather than hoping Enter picked the right one: read the
    # menu, prefer an option whose label starts with the answer, and report the
    # label that was actually taken.
    offered = element.evaluate(
        """
        () => {
          const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
          const menu = document.querySelector('[class*="select__menu"]');
          if (!menu) return [];
          return Array.from(menu.querySelectorAll('[role="option"]'))
            .map((option) => clean(option.innerText))
            .filter(Boolean)
            .slice(0, 12);
        }
        """
    )
    match = pick_option(offered, value)
    if match:
        try:
            page.keyboard.press("Enter")
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(300)
        return match
    try:
        page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _select_by_text(element, value: str) -> None:
    """Choose the option whose label matches, falling back to a case-insensitive scan."""
    try:
        element.select_option(label=value)
        return
    except Exception:  # noqa: BLE001
        pass
    options = [
        ((option.inner_text() or "").strip(), option.get_attribute("value") or "")
        for option in element.query_selector_all("option")
    ]
    for text, option_value in options:
        if text.lower() == value.lower():
            element.select_option(value=option_value or text)
            return
    for text, option_value in options:
        if text and (value.lower() in text.lower() or text.lower() in value.lower()):
            element.select_option(value=option_value or text)
            return
    available = ", ".join(text for text, _ in options if text) or "none"
    raise LookupError(f"no option matching {value!r} (offers: {available})")


def _click_option(page, element, value: str) -> bool:
    """Click the radio or checkbox in this group whose label matches."""
    name = element.get_attribute("name") or ""
    group = page.query_selector_all(f"input[name='{name}']") if name else [element]
    for candidate in group:
        label = candidate.evaluate(
            """
            (el) => {
              const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
              if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
              return clean(el.value);
            }
            """
        )
        if label.lower().strip(" .*") == value.lower().strip(" .*"):
            if _press_option(candidate):
                return True
    return False


def _press_option(candidate) -> bool:
    """Set a radio or checkbox the way the page expects it to be set.

    ``check()`` is right for an ordinary input. Workable hides the input and
    gives the visible role to a wrapper div, so the click has to land on that
    wrapper or nothing reaches the page's own handler.
    """
    try:
        candidate.check(timeout=2_000)
        return True
    except Exception:  # noqa: BLE001
        pass
    for expression in (
        "el => el.closest('label')",
        "el => el.closest(\"[role='radio'], [role='checkbox']\")",
    ):
        try:
            target = candidate.evaluate_handle(expression).as_element()
        except Exception:  # noqa: BLE001
            target = None
        if target is None:
            continue
        try:
            target.click(timeout=2_000)
            return True
        except Exception:  # noqa: BLE001
            continue
    try:
        candidate.click(force=True, timeout=2_000)
        return True
    except Exception:  # noqa: BLE001
        return False


def context_for(config: dict, pack, evidence: list[str] | None = None):
    """The question context for one application."""
    from . import questions

    return questions.Context(
        market=pack.market,
        company=pack.job["company"],
        role=pack.job["title"],
        answers=config.get("answers", {}),
        profile=config.get("profile", {}),
        candidate=config.get("candidate", {}),
        evidence=evidence if evidence is not None else list(pack.evidence),
        disclosures=config.get("disclosures", {}),
    )


def required_still_empty(page) -> list[str]:
    """Labels of required fields that are still blank, by the page's own markup."""
    return page.evaluate(
        """
        () => {
          const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
          const labelOf = (el) => {
            const kind = (el.type || '').toLowerCase();
            const fieldset = el.closest('fieldset');
            if ((kind === 'radio' || kind === 'checkbox') && fieldset) {
              const legend = fieldset.querySelector('legend');
              if (legend && clean(legend.innerText)) return clean(legend.innerText);
              const ids = fieldset.getAttribute('aria-labelledby') || '';
              const named = clean(ids.split(/\\s+/).map((id) => {
                const node = id ? document.getElementById(id) : null;
                return node ? node.innerText : '';
              }).join(' '));
              if (named) return named;
            }
            const entry = el.closest('[data-field-path]');
            if (entry) {
              const own = entry.querySelector('label');
              if (own && clean(own.innerText)) return clean(own.innerText);
            }
            // react-select keeps a hidden state input next to the visible
            // combobox; the question belongs to that combobox's label.
            const shell = el.closest('[class*="select-shell"]');
            if (shell) {
              const combo = shell.querySelector('input[role="combobox"], input[id]');
              if (combo && combo.labels && combo.labels.length) return clean(combo.labels[0].innerText);
            }
            return el.labels && el.labels.length ? clean(el.labels[0].innerText) : (el.name || el.id);
          };
          const out = [];
          for (const el of document.querySelectorAll('input, select, textarea')) {
            if (!el.required || el.disabled) continue;
            const kind = (el.type || '').toLowerCase();
            // A radio group marks every input required, and an unchecked radio
            // still carries a value attribute, so "value is non-empty" would
            // call a skipped knockout question answered.
            const empty = (kind === 'radio' || kind === 'checkbox')
              ? !el.checked
              : (!el.value || (kind === 'file' && !el.files.length));
            if (!empty) continue;
            const label = labelOf(el).replace(/\\s*\\*\\s*$/, '').trim();
            // An unlabelled control is still listed, but by its name or id, not
            // as an empty string a reader cannot act on.
            out.push(label || el.name || el.id || el.getAttribute('placeholder') || 'unlabelled field');
          }
          return out;
        }
        """
    )


def run(
    url: str,
    values: Values,
    *,
    ats: str = "",
    ctx=None,
    submit: bool = False,
    advance: bool = False,
    headless: bool = True,
    hold: bool | object = False,
    screenshot_dir: Path | None = None,
    timeout_ms: int = 45_000,
    account=None,
    home: Path | None = None,
    create_account: bool = False,
    on_code=None,
    mail_config: dict | None = None,
) -> FillReport:
    """Open a form, fill it, screenshot it, and optionally submit.

    With an ``account`` (see :mod:`jobhunter.accounts`) a Workday run will sign
    in with the stored password, create the account if asked, and reuse the
    cookies from last time so the second application at that employer is one
    page shorter.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return FillReport(url=url, ats=ats, error="playwright is not installed")

    from . import resolve as handoff

    kind = ats or detect_ats(url)
    #: why the URL changed, when it did: an aggregator's Apply link was followed
    handoff_note = ""

    if not kind:
        if handoff.looks_like_aggregator(url):
            outcome = handoff.resolve(url, headless=headless, timeout_ms=timeout_ms)
            if outcome.closed:
                return FillReport(
                    url=url,
                    ats="",
                    error=(
                        "the listing has closed: Adzuna/Reed is saying the job is no "
                        "longer available, so there is no form to send. Find the role "
                        "on the employer's own site or through the agency."
                    ),
                )
            if outcome.final_url and detect_ats(outcome.final_url):
                url = outcome.final_url
                kind = detect_ats(url)
                handoff_note = f"followed the listing's Apply link to {kind}"
            elif outcome.final_url and not outcome.moved:
                # The button did something in place (a modal, a sign-in) rather
                # than handing off to a page with a form.
                host = outcome.host or urlsplit(url).netloc.lower()
                known = handoff.account_board_note(host)
                detail = (
                    known
                    if known
                    else f"{host or 'this site'} keeps the application on its own page"
                )
                return FillReport(
                    url=url,
                    ats="",
                    error=(
                        f"{detail}, so there is no employer form to fill from this link. "
                        f"Apply on {host or 'the site'} itself, or find the role on the "
                        "employer's own board."
                    ),
                )
            elif outcome.final_url:
                return FillReport(
                    url=outcome.final_url,
                    ats="",
                    error=(
                        f"the application happens on {outcome.host or 'another site'}, "
                        f"which has no filler yet: {outcome.final_url}"
                    ),
                )
            else:
                return FillReport(
                    url=url,
                    ats="",
                    error=f"could not follow this listing's Apply link ({outcome.note})",
                )

    filler_class = FILLERS.get(kind)
    if filler_class is None and not ats and url.startswith("http"):
        # A short link (grnh.se, appcast, a tracking domain) hides the board.
        # Follow it once, then look at where it landed rather than at the alias.
        landed = handoff.landing(url, headless=headless, timeout_ms=timeout_ms)
        landed_kind = detect_ats(landed)
        if landed_kind in FILLERS:
            url = landed
            kind = landed_kind
            filler_class = FILLERS[landed_kind]
            handoff_note = f"followed a short link to {landed_kind}"

    if filler_class is None:
        return FillReport(
            url=url,
            ats=kind,
            error=(
                f"no filler for {kind or 'this site'} yet; "
                f"known fillers: {', '.join(sorted(FILLERS))}"
            ),
        )
    form = filler_class()

    # Read the account once, before a browser opens: the password is also the
    # string that has to be scrubbed out of the trace.
    email = ""
    password = ""
    if account is not None:
        from . import accounts as accounts_module

        email = account.email
        password = accounts_module.password(account)

    state_path = None
    if account is not None and home is not None:
        from . import accounts as accounts_module

        state_path = accounts_module.session_path(home, account)
        state_path.parent.mkdir(parents=True, exist_ok=True)

    # Every run leaves a trace: requests with secrets masked, console messages,
    # the DOM, a screenshot and a summary. A screenshot alone cannot tell you
    # whether the form ever sent anything.
    trace = None
    if home is not None:
        from . import evidence as evidence_module

        trace = evidence_module.new_trace(
            home, f"fill-{kind or 'form'}", secrets=(password,)
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=UA,
            locale="en-GB",
            viewport={"width": 1280, "height": 1600},
            storage_state=str(state_path) if state_path and state_path.is_file() else None,
        )
        page = context.new_page()
        report = FillReport(url=url, ats=kind)
        if trace is not None:
            trace.attach(context, page)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if trace is not None:
                trace.note("goto", url)
            page.wait_for_timeout(1200)
            report.url = page.url
            login = Login(
                email=email,
                password=password,
                create=create_account,
                on_code=on_code,
            )
            # One call. No `if kind == "workday"`: a wizard overrides fill, a
            # single-page form implements fill_form, and run() never needs to
            # know which it is holding.
            report = form.fill(page, values, ctx, advance=advance, login=login)
            if handoff_note:
                report.notes.insert(0, handoff_note)
            if not report.error and not (
                report.filled or report.steps or report.questions_answered
            ):
                # A closed posting redirects to a board index, and a page that
                # never rendered looks the same from here: no controls, no
                # error. Saying "nothing to fill" is the honest report.
                report.error = (
                    f"no form fields found at {page.url}; the posting may be closed "
                    "or the page did not render"
                )
            if report.sign_in:
                if trace is not None:
                    trace.note("account", report.sign_in)
                if report.sign_in in ("created", "signed-in") and account is not None and home is not None:
                    from . import accounts as accounts_module

                    tenant = accounts_module.tenant_from_url(page.url)
                    if tenant:
                        accounts_module.upsert_tenant(home, account.firm, tenant)
            report.ats = kind
            report.url = page.url
            report.required_left = required_still_empty(page)
            report.dedupe()
            if trace is not None:
                trace.note(
                    "filled",
                    f"{len(report.filled)} fields, {len(report.empty_answers)} unanswerable",
                )

            if state_path is not None:
                try:
                    context.storage_state(path=str(state_path))
                except Exception:  # noqa: BLE001
                    pass

            if screenshot_dir is not None:
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                shot = screenshot_dir / f"{kind}-{int(time.time())}.png"
                page.screenshot(path=str(shot), full_page=True)
                report.screenshot = str(shot)

            if submit:
                reason = form.submit(page)
                report.submitted = not reason
                report.url = page.url
                if trace is not None:
                    trace.note("submit", reason or "the page moved after submit")
                if reason:
                    report.error = reason

            if hold:
                # The browser closes at the end of every run, which leaves no way
                # to finish the parts this tool deliberately will not touch:
                # disclosures, a date, a cover letter. With hold the window stays
                # open and the run ends when you say so, not when a timer does.
                # The dashboard passes an Event; the terminal passes input().
                print(
                    "\n  the browser is open. Finish or submit the application there, "
                    "then press Enter here to close it."
                )
                try:
                    if callable(hold):
                        hold()
                    else:
                        input()
                except (EOFError, KeyboardInterrupt):
                    pass
                report.url = page.url
                report.required_left = required_still_empty(page)
        except Exception as error:  # noqa: BLE001
            report = FillReport(
                url=url, ats=kind, error=f"{type(error).__name__}: {error}"
            )
        finally:
            if trace is not None:
                try:
                    report.trace_path = str(
                        trace.save(
                            page,
                            outcome=report.error or report.sign_in or "no error reported",
                        )
                    )
                except Exception:  # noqa: BLE001
                    pass
            context.close()
            browser.close()

    return report


def _page_signature(page) -> str:
    """A cheap fingerprint of what the page says, to notice a real change."""
    try:
        return page.evaluate(
            "() => (document.body ? document.body.innerText : '').slice(0, 4000)"
        )
    except Exception:  # noqa: BLE001
        return ""


def _until_page_moves(page, before_url: str, before_page: str, seconds: float = 8.0) -> bool:
    """Did the page actually change? A dispatched click is not a submission."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if page.url != before_url or _page_signature(page) != before_page:
            return True
        page.wait_for_timeout(300)
    return page.url != before_url or _page_signature(page) != before_page
