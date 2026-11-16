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
    screenshot: str = ""
    submitted: bool = False
    error: str = ""

    def summary(self) -> str:
        lines = [f"{self.ats or 'unknown ats'}: {self.url}"]
        if self.error:
            lines.append(f"  error: {self.error}")
            return "\n".join(lines)
        lines.append(f"  filled:  {', '.join(self.filled) or 'nothing'}")
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


FILLERS = {"greenhouse": Greenhouse, "lever": Lever, "questions": GenericQuestions}


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
    headless: bool = True,
    screenshot_dir: Path | None = None,
    timeout_ms: int = 45_000,
) -> FillReport:
    """Open a form, fill it, screenshot it, and optionally submit."""
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

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=UA, locale="en-GB", viewport={"width": 1280, "height": 1600})
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1200)
            report = filler().fill(page, values, ctx)
            report.ats = kind
            report.url = page.url
            report.required_left = required_still_empty(page)

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
