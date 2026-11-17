"""Answer a form's questions, deterministically, from the answer bank.

Two kinds of question appear on an application form, and they deserve different
treatment:

* **Facts.** "Do you require sponsorship?" on a UK role is not a judgement call.
  It is a value in the answer bank, and answering it is just transcription.
* **Words.** "Why this role?" is writing. It is answered from a library of
  paragraphs you wrote, with the company and role substituted in — not generated
  afresh, because a fresh paragraph is one you have not read.

Everything else — salary expectations with nothing in the bank, a field asking
for a profile that does not exist, anything about gender, ethnicity, veteran
status, disability, religion or criminal history — is left alone and reported.
A blank field that the employer's own validation rejects is a much better outcome
than a confident invention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Never answered, whatever the bank says. These are declarations about a person,
# not data entry, and getting one wrong is worse than leaving the form empty.
PROTECTED = re.compile(
    r"\b(gender|sex\b|ethnic\w*|race\b|racial|veteran|disabilit\w*|religio\w*|"
    r"sexual orientation|date of birth|\bage\b|criminal|conviction|"
    r"background check|drug|marital|nationality)\b",
    re.I,
)

# Which voluntary-disclosure answer a protected question would use, if the
# candidate stored one. Nothing is stored by default.
DISCLOSURE_KEYS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"gender|sex\b", re.I), "gender"),
    (re.compile(r"ethnic|race\b|racial", re.I), "ethnicity"),
    (re.compile(r"veteran", re.I), "veteran_status"),
    (re.compile(r"disabilit", re.I), "disability"),
)


@dataclass
class Context:
    """What an answer may depend on."""

    market: str = "uk"
    company: str = ""
    role: str = ""
    answers: dict = field(default_factory=dict)
    profile: dict = field(default_factory=dict)
    candidate: dict = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    disclosures: dict = field(default_factory=dict)

    def get(self, key: str, default: str = "") -> str:
        return str(self.answers.get(key, default) or default)

    def get_disclosure(self, key: str) -> str:
        """A voluntary-disclosure answer, only if the candidate stored one."""
        return str(self.disclosures.get(key, "") or "")


@dataclass(frozen=True)
class Answer:
    value: str
    confidence: str  # "certain" (a fact from the bank) or "review" (needs a human)
    reason: str


def question_text(element) -> str:
    """The words a person would read as the question.

    For a radio group the question is the fieldset legend; the radio's own label
    is the option ("Yes", "No"), which would be useless as a lookup key.
    """
    return element.evaluate(
        """
        (el) => {
          const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
          const type = (el.type || '').toLowerCase();
          const fieldset = el.closest('fieldset');
          if ((type === 'radio' || type === 'checkbox') && fieldset) {
            const legend = fieldset.querySelector('legend');
            if (legend) return clean(legend.innerText);
          }
          if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
          if (fieldset) {
            const legend = fieldset.querySelector('legend');
            if (legend) return clean(legend.innerText);
          }
          const aria = el.getAttribute('aria-label') || el.getAttribute('placeholder');
          if (aria) return clean(aria);
          return clean(el.getAttribute('name') || el.id || '');
        }
        """
    )


def widget_of(element) -> str:
    return element.evaluate(
        """
        (el) => {
          const tag = el.tagName.toLowerCase();
          if (tag === 'select') return 'select';
          if (tag === 'textarea') return 'text';
          if (tag === 'input') return (el.type || 'text').toLowerCase();
          return 'text';
        }
        """
    )


# --- fact rules ----------------------------------------------------------

YES = Answer("Yes", "certain", "answer bank: right to work held, no sponsorship needed")
NO = Answer("No", "certain", "answer bank: no sponsorship needed")

# Places a question can name. A form that asks about work authorisation in the
# United States is not asking about the market this candidate can work in, and
# answering "Yes" because the answer bank says "no sponsorship needed" would be a
# false statement on a legal declaration.
COUNTRIES: dict[str, tuple[str, ...]] = {
    "uk": ("united kingdom", "great britain", "britain", "england", "scotland", "wales", "northern ireland", "uk"),
    "au": ("australia",),
    "us": ("united states", "usa", "u.s.", "u.s.a.", "america"),
    "ca": ("canada",),
    "ie": ("ireland",),
    "de": ("germany",),
    "fr": ("france",),
    "nl": ("netherlands",),
    "sg": ("singapore",),
    "hk": ("hong kong",),
    "in": ("india",),
    "ae": ("united arab emirates", "dubai", "abu dhabi"),
}


# Bare "US" is ambiguous — "why do you want to work for us" is not a country — so
# it is matched by context rather than as a word.
US_CONTEXT = re.compile(
    r"\b(?:in|the|to|for|across|within)\s+(?:the\s+)?us\b|\bu\.s\.|\bus[ -]based\b|\bus\s+work\b",
    re.I,
)


def country_in(text: str) -> str:
    """Which country a question names, or '' when it names none.

    Punctuation is normalised to spaces first: a question ends with "?", so the
    naive " United States " test missed "United States?".
    """
    lowered = re.sub(r"[^a-z0-9.']+", " ", text.lower()).strip()
    padded = f" {lowered} "
    for code, words in COUNTRIES.items():
        for word in words:
            if f" {word} " in padded:
                return code
    if US_CONTEXT.search(text):
        return "us"
    return ""


def _rule_sponsorship(label: str, ctx: Context) -> Answer:
    if ctx.candidate.get("needs_sponsorship"):
        return Answer("Yes", "review", "config says this candidate needs sponsorship")
    named = country_in(label)
    if named and named != ctx.market:
        return Answer(
            "",
            "review",
            f"question names {named.upper()}; you are not entitled to work there, so this is your call",
        )
    return NO


def _rule_authorised(label: str, ctx: Context) -> Answer:
    if ctx.candidate.get("needs_sponsorship"):
        return Answer("No", "review", "config says this candidate needs sponsorship")
    named = country_in(label)
    if named and named != ctx.market:
        return Answer(
            "",
            "review",
            f"question names {named.upper()}; you are not authorised to work there",
        )
    return YES


def _rule_salary(label: str, ctx: Context) -> Answer:
    # An internship pays a stipend, and a salary expectation on a summer
    # placement reads as someone who misread the advert. Leave it for a human.
    if re.search(r"\bintern(ship)?s?\b|\bplacement\b|\bsummer\b|work experience", ctx.role or "", re.I):
        return Answer("", "review", "internship or placement: no salary expectation stated")
    value = ctx.get("salary_expectation")
    if not value:
        return Answer("", "review", "no salary expectation in the answer bank")
    return Answer(value, "review", "answer bank: salary_expectation; check it suits this role")


RULES: tuple[tuple[re.Pattern[str], object], ...] = (
    (
        re.compile(r"(require|need).{0,30}sponsor|sponsorship", re.I),
        _rule_sponsorship,
    ),
    (
        re.compile(r"authori[sz]ed to work|right to work|legally (?:able|entitled|permitted) to work", re.I),
        _rule_authorised,
    ),
    (
        re.compile(r"notice period|when can you start|available to start|earliest start", re.I),
        lambda label, ctx: _from_bank(ctx, "notice_period"),
    ),
    (
        re.compile(r"salary|compensation expectation|remuneration|day rate", re.I),
        _rule_salary,
    ),
    (
        re.compile(r"relocat", re.I),
        lambda label, ctx: _from_bank(ctx, "willing_to_relocate"),
    ),
    (
        re.compile(r"how did you (?:hear|find)|where did you (?:hear|see)|source of application", re.I),
        lambda label, ctx: _from_bank(ctx, "how_did_you_hear", "no source recorded"),
    ),
    (
        re.compile(r"linkedin|profile url|website|portfolio|github", re.I),
        lambda label, ctx: _from_bank(ctx, "linkedin", "no profile URL in the answer bank"),
    ),
    (
        re.compile(r"current (?:employer|company)|present employer", re.I),
        lambda label, ctx: _from_bank(ctx, "current_employer", "no current employer recorded"),
    ),
    (
        re.compile(r"current (?:job )?title|present position|job title", re.I),
        lambda label, ctx: _from_bank(ctx, "current_title", "no current title recorded"),
    ),
    (
        re.compile(r"highest (?:level of )?education|highest qualification|degree", re.I),
        lambda label, ctx: _from_bank(ctx, "highest_qualification"),
    ),
    (
        re.compile(r"professional qualification|certification|chartered", re.I),
        lambda label, ctx: _from_bank(ctx, "professional_qualifications"),
    ),
    (
        re.compile(r"years of experience|how many years", re.I),
        lambda label, ctx: _years(ctx),
    ),
)

FREE_TEXT = re.compile(
    r"why (?:do you want|are you interested|this role|us\b|our company|join)|"
    r"cover letter|anything else|additional information|tell us about|"
    r"what (?:attracts|interests) you|motivat",
    re.I,
)

# Which library paragraph a free-text question wants. Order matters: "why do you
# want to work for us" is about the firm, and would otherwise match the role rule
# first.
FREE_TEXT_KEYS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"why (?:do you want to )?(?:work (?:for|with) )?(?:us|our (?:company|firm|organisation|organization))|"
            r"what do you know about|what attracts you to (?:us|the firm)",
            re.I,
        ),
        "why_company",
    ),
    (
        re.compile(
            r"why (?:do you want|are you interested in?|this role|apply)|what attracts|motivat",
            re.I,
        ),
        "why_role",
    ),
    (re.compile(r"strength|what makes you|why should we", re.I), "strengths"),
    (re.compile(r"career|five years|goals|aspiration", re.I), "career_goals"),
    (re.compile(r"anything else|additional|gap|anything we should know", re.I), "additional"),
)


def _from_bank(ctx: Context, key: str, missing_reason: str = "") -> Answer:
    value = ctx.get(key)
    if not value:
        return Answer("", "review", missing_reason or f"no answer for {key} in the bank")
    return Answer(value, "certain", f"answer bank: {key}")


def _years(ctx: Context) -> Answer:
    years = ctx.candidate.get("years_experience")
    if years is None:
        return Answer("", "review", "no years_experience in config")
    return Answer(str(int(years)) if float(years).is_integer() else str(years), "certain", "config: years_experience")


def answer_for(label: str, ctx: Context) -> Answer | None:
    """An answer for a question, or None when nothing sensible can be said."""
    text = " ".join((label or "").split())
    if not text:
        return None
    if PROTECTED.search(text):
        for pattern, key in DISCLOSURE_KEYS:
            if pattern.search(text):
                stored = ctx.get_disclosure(key)
                if stored:
                    return Answer(stored, "review", f"your stored {key} answer; check it before submitting")
        return Answer("", "review", "protected question: yours to answer, never guessed")

    for pattern, rule in RULES:
        if pattern.search(text):
            return rule(text, ctx)

    if FREE_TEXT.search(text):
        return _library_answer(text, ctx)
    return None


def _library_answer(label: str, ctx: Context) -> Answer:
    library = ctx.answers.get("free_text")
    if not isinstance(library, dict):
        return Answer("", "review", "free-text question: no answer library in config")

    for pattern, key in FREE_TEXT_KEYS:
        if pattern.search(label):
            template = str(library.get(key, "") or "")
            if not template:
                return Answer("", "review", f"free-text question: no '{key}' paragraph written yet")
            return Answer(render(template, ctx), "review", f"your '{key}' paragraph; read it before submitting")

    return Answer("", "review", "free-text question: no matching paragraph in the library")


def render(template: str, ctx: Context) -> str:
    """Substitute the few placeholders the library may use.

    Deliberately a fixed vocabulary rather than a template language: an answer
    that can execute logic is an answer you cannot review at a glance.
    """
    evidence = " ".join(ctx.evidence[:2]) if ctx.evidence else ""
    return (
        template.replace("{company}", ctx.company)
        .replace("{role}", ctx.role)
        .replace("{market}", "the UK" if ctx.market == "uk" else "Australia")
        .replace("{evidence}", evidence)
        .strip()
    )
