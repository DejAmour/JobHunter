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

from . import db

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ATS_HOSTS = (
    ("greenhouse", ("greenhouse.io",)),
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
    steps: list[str] = field(default_factory=list)
    sign_in: str = ""
    screenshot: str = ""
    submitted: bool = False
    error: str = ""

    def summary(self) -> str:
        lines = [f"{self.ats or 'unknown ats'}: {self.url}"]
        if self.error:
            lines.append(f"  error: {self.error}")
            return "\n".join(lines)
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
        if self.required_left:
            lines.append(f"  still required: {', '.join(self.required_left)}")
        lines.append(f"  submitted: {'yes' if self.submitted else 'no'}")
        if self.screenshot:
            lines.append(f"  screenshot: {self.screenshot}")
        return "\n".join(lines)


# --- per-ATS fillers -----------------------------------------------------


class Greenhouse:
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
    SELECT = {"country": "#country"}
    FILE = {"resume": "#resume"}

    def fill(self, page, values: Values, ctx=None) -> FillReport:
        report = FillReport(url=page.url, ats=self.name)
        self._text(page, values, report)
        self._select(page, values, report)
        self._file(page, values, report)
        if ctx is not None:
            fill_questions(
                page,
                ctx,
                'input[id^="question_"], select[id^="question_"], textarea[id^="question_"]',
                report,
            )
        return report

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


class Lever:
    name = "lever"

    TEXT = {
        "name": "input[name='name']",
        "email": "input[name='email']",
        "phone": "input[name='phone']",
        "org": "input[name='org']",
    }
    FILE = {"resume": "input[name='resume'], #resume-upload-input"}

    def fill(self, page, values: Values, ctx=None) -> FillReport:
        report = FillReport(url=page.url, ats=self.name)
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
        return report


class GenericQuestions:
    """Answers whatever question fields a page shows, and nothing else.

    Used for pages whose standard fields a dedicated filler already handled —
    Workday's question step is the obvious case — and for exercising the question
    engine against a fixture.
    """

    name = "questions"

    def fill(self, page, values: Values, ctx=None) -> FillReport:
        report = FillReport(url=page.url, ats=self.name)
        if ctx is not None:
            fill_questions(
                page,
                ctx,
                "form input, form select, form textarea",
                report,
            )
        return report


class Workday:
    """Workday's wizard, one step at a time.

    Workday is five pages behind an account: My Information, My Experience,
    Application Questions, Voluntary Disclosures, Review. This clicks Apply,
    joins the account wall with the password from the Credential Manager, creates
    the account if asked and a verification code can be read, then fills whichever
    step is showing and — with ``advance`` — walks to the next one, stopping at
    Review. It does not submit.
    """

    name = "workday"

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
        ("my-information", ("[data-automation-id='legalNameSection_firstName']", "#firstName")),
        ("my-experience", ("[data-automation-id='workExperienceSection']", "#jobTitle")),
        ("questions", ("[data-automation-id='applicationQuestions']", "fieldset legend")),
        ("disclosures", ("[data-automation-id='voluntaryDisclosures']", "[data-automation-id='selfIdentify']")),
        ("review", ("[data-automation-id='reviewPage']", "[data-automation-id='submitButton']")),
    )

    # Real Workday automation ids, with the fixture ids alongside them.
    FIELDS = {
        "first_name": "[data-automation-id='legalNameSection_firstName'], #firstName",
        "last_name": "[data-automation-id='legalNameSection_lastName'], #lastName",
        "email": "input[data-automation-id='email'], #email",
        "phone": "input[data-automation-id='phone-number'], #phone",
        "address": "[data-automation-id='addressSection_addressLine1'], #address",
        "city": "[data-automation-id='addressSection_city'], #city",
        "postcode": "[data-automation-id='addressSection_postalCode'], #postalCode",
        "linkedin": "[data-automation-id='linkedin'], #linkedin",
    }
    COUNTRY = "[data-automation-id='addressSection_countryRegion'], #country"
    RESUME = "input[data-automation-id='file-upload-input-ref'], input#resume, input[name='resume']"
    NEXT = "[data-automation-id='pageFooterNextButton'], button[data-action='continue']"

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

    def fill(
        self,
        page,
        values: Values,
        ctx=None,
        *,
        advance: bool = False,
        steps: int = 6,
        email: str = "",
        password: str = "",
        create_account: bool = False,
        on_code=None,
    ) -> FillReport:
        report = FillReport(url=page.url, ats=self.name)
        data = values.as_dict()

        # From a job posting: accept cookies, Apply, Apply Manually, then step 1.
        # A page that is already a form returns immediately.
        self.prepare(page)

        for _ in range(max(1, steps)):
            step = self._settle(page)
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
            elif step == "my-experience":
                self._resume(page, data, report)
            elif step == "questions":
                if ctx:
                    fill_questions(page, ctx, "form input, form select, form textarea, fieldset legend", report)
            elif step == "disclosures":
                report.questions_review.append(
                    ("Voluntary disclosures", "protected: never auto-answered")
                )
            else:
                # Review, or a page this filler does not know: stop rather than
                # click through something it cannot read.
                return report

            if not advance:
                return report

            button = page.query_selector(self.NEXT)
            if button is None:
                return report
            button.click()
            page.wait_for_timeout(2500)

        return report

    def _fields(self, page, data, report):
        for key, selector in self.FIELDS.items():
            element = self._one(page, selector)
            if element is None:
                continue
            value = data.get(key, "")
            if not value:
                report.empty_answers.append(key)
                continue
            element.fill(value)
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
        element = self._one(page, self.RESUME)
        if element is None:
            return
        path = data.get("resume", "")
        if not path or not Path(path).is_file():
            report.empty_answers.append("resume")
            return
        element.set_input_files(path)
        # Workday parses the CV and fills the history with it; the parser is
        # decent and wrong in places, which is why the review step exists.
        page.wait_for_timeout(4000)
        report.filled.append("resume")

    @staticmethod
    def _one(page, selector):
        try:
            return page.query_selector(selector)
        except Exception:  # noqa: BLE001
            return None

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

        apply_button = self._await(page, self.APPLY, timeout_ms)
        if apply_button is not None:
            try:
                apply_button.click()
            except Exception:  # noqa: BLE001
                page.evaluate("el => el.click()", apply_button)
            page.wait_for_timeout(1500)

        # The shelf: prefer the manual route. Autofill parses the CV into work
        # history, and its guesses are harder to check than an empty form.
        manual = self._await(page, self.APPLY_MANUALLY, 20_000)
        if manual is not None:
            try:
                manual.click()
            except Exception:  # noqa: BLE001
                page.evaluate("el => el.click()", manual)
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
                toggle.click()
                page.wait_for_timeout(wait_ms)
            fields = self.CREATE
            outcome = "created"
        else:
            if step != "sign-in" and self._clickable(page, self.SIGNIN["submit"]) is None:
                toggle = self._clickable(page, self.TO_SIGNIN)
                if toggle is None:
                    return "failed: no Sign In form on this page"
                toggle.click()
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
                        terms.check()
                except Exception:  # noqa: BLE001
                    pass

        submit = self._clickable(page, fields["submit"])
        if submit is None:
            return "failed: no submit button on the account form"
        submit.click()
        page.wait_for_timeout(wait_ms + 2500)

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
                submit.click()
                page.wait_for_timeout(wait_ms + 2500)

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

    def _dismiss_cookies(self, page) -> None:
        for selector in self.COOKIES:
            try:
                button = page.query_selector(selector)
                if button is not None and button.is_visible():
                    button.click(timeout=4000)
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
        try:
            if element.get_attribute("data-automation-id") in Workday.HONEYPOT_IDS:
                return True
            label = (element.get_attribute("aria-label") or "").lower()
            if "robots only" in label or "do not enter" in label:
                return True
            box = element.bounding_box()
            if box is not None and (box["height"] < 2 or box["width"] < 2):
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


def _type(element, value: str) -> None:
    """Put text in a field the way a person does.

    Workday's React inputs ignore a bare value assignment often enough that the
    autofill community has standardised on real key events; ``fill`` dispatches
    the input event React listens for, and the keystrokes cover the components
    that only listen to those.
    """
    if (element.get_attribute("data-automation-id") or "") in Workday.HONEYPOT_IDS:
        raise ValueError("refusing to type into Workday's bot-catcher field")
    try:
        element.click()
        element.fill("")
        element.fill(value)
        element.dispatch_event("change")
    except Exception:  # noqa: BLE001
        element.fill(value)


FILLERS = {
    "greenhouse": Greenhouse,
    "lever": Lever,
    "workday": Workday,
    "questions": GenericQuestions,
}


def fill_questions(page, ctx, selector: str, report: FillReport) -> None:
    """Answer whichever custom questions the page shows, from the answer bank.

    Works on any markup: it reads the question from the label or the fieldset
    legend, so the same code answers a Greenhouse ``#question_*`` field, a Lever
    ``cards[...]`` field and a Workday fieldset.
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
        answer = questions.answer_for(label, ctx)
        if answer is None:
            # Not a question this engine claims to answer. Silence is right here;
            # required_still_empty() reports it if the employer insists.
            continue
        if answer.confidence != "certain" or not answer.value:
            report.questions_review.append((label, answer.reason))
            continue

        try:
            if widget == "select":
                _select_by_text(element, answer.value)
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
            candidate.check()
            return True
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
          const out = [];
          for (const el of document.querySelectorAll('input, select, textarea')) {
            if (!el.required || el.disabled) continue;
            const empty = !el.value || (el.type === 'file' && !el.files.length);
            if (!empty) continue;
            const label = el.labels && el.labels.length ? el.labels[0].innerText : (el.name || el.id);
            out.push(label.replace(/\\s*\\*\\s*$/, '').trim());
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

    kind = ats or detect_ats(url)
    filler = FILLERS.get(kind)
    if filler is None:
        return FillReport(
            url=url,
            ats=kind,
            error=f"no filler for {kind or 'this site'} yet; greenhouse and lever are done",
        )

    state_path = None
    if account is not None and home is not None:
        from . import accounts as accounts_module

        state_path = accounts_module.session_path(home, account)
        state_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=UA,
            locale="en-GB",
            viewport={"width": 1280, "height": 1600},
            storage_state=str(state_path) if state_path and state_path.is_file() else None,
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1200)
            report = FillReport(url=page.url, ats=kind)
            if kind != "workday":
                report = filler().fill(page, values, ctx)
            if kind == "workday":
                # Workday is a wizard with an account wall in front of it, so it
                # gets the whole run to itself rather than a generic pass first.
                password = ""
                email = ""
                if account is not None:
                    from . import accounts as accounts_module

                    email = account.email
                    password = accounts_module.password(account)
                report = Workday().fill(
                    page,
                    values,
                    ctx,
                    advance=advance,
                    email=email,
                    password=password,
                    create_account=create_account,
                    on_code=on_code,
                )
                if report.sign_in in ("created", "signed-in") and account is not None and home is not None:
                    from . import accounts as accounts_module

                    tenant = accounts_module.tenant_from_url(page.url)
                    if tenant:
                        accounts_module.upsert_tenant(home, account.firm, tenant)
            report.ats = kind
            report.url = page.url
            report.required_left = required_still_empty(page)

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
                button = page.query_selector(
                    "button[type='submit'], #btn-submit, [data-testid='submit-application']"
                )
                if button is None:
                    report.error = "no submit control found"
                else:
                    button.click()
                    page.wait_for_timeout(3000)
                    report.submitted = True
                    report.url = page.url
        except Exception as error:  # noqa: BLE001
            report = FillReport(url=url, ats=kind, error=f"{type(error).__name__}: {error}")
        finally:
            context.close()
            browser.close()

    return report
