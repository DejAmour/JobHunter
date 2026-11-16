# Design

Three decisions shaped this. Each is written down with the reason, so it can be
argued with later rather than rediscovered.

## 1. Adopt, do not rebuild

Two projects already solve the expensive part — driving a real browser through
a dozen applicant-tracking systems. Rebuilding that is months of work to reach
the same place. The split:

| Taken from | What | Why that one |
|---|---|---|
| AIHawk | `vendor/aihawk/plugins/*.md` | A plugin is prose, not code. It names selectors and failure modes and lets the model adapt. Cheaper to maintain than 12 DOM adapters, and it already covers Workday. |
| AIHawk | Title-graph search plan | Deterministic keyword scoring is cheap and inspectable; only the top ~100 postings go to a model. |
| AIHawk | Screenshots + `data/trace.log` per send | Proof of what was submitted, and a way to debug a form after the fact. |
| JobAppAgent | OS credential store | Workday needs a per-company password. Storing it in a local DB is the one thing that would make a leak catastrophic. Use Windows Credential Manager / DPAPI, never a column. |
| JobAppAgent | `vendor/jobappagent/fixtures/` | Real Greenhouse/Ashby/Lever/Workday HTML. Lets every selector be tested without submitting to an employer. |
| JobAppAgent | Answer bank | A stable set of facts about the candidate. Postings and forms are untrusted input; the answer bank is the only source of truth for what gets written into a form. |
| JobAppAgent | Duplicate, requisition and cooldown gates | Re-applying to the same req is the most common way to get filtered. |

Rejected from AIHawk: its model gateway. `DEFAULT_BASE_URL` is hardcoded to one
vendor, the scoring path uses a non-portable endpoint, and its key URL is the
author's referral link. Also rejected: its telemetry, which the author documents
in code as impossible to disable and deliberately absent from the README.

Rejected from JobAppAgent: nothing structurally, but its CLI surface is large.
We take the ideas and the fixtures, not the whole scaffold.

## 2. Screening before effort

The highest-leverage thing in the pipeline is not filling forms faster. It is
refusing to fill the wrong ones.

An ATS front-end tells you the *form*. It does not tell you the *screening*,
which is server-side: a CV is parsed into structured fields and ranked against
the requisition. That logic is not reachable from the browser and guessing at it
is wasted effort.

What is reachable — and is what actually rejects applications silently — are
**knockout questions**: the hard filters a recruiter configures inside the form.

- right to work / visa sponsorship
- security clearance or a licence
- minimum years, often stated twice
- a salary floor the form makes you accept
- work mode (fully on-site vs the claim in the advert)
- an excluded location

`screening.py` extracts these from the posting text deterministically, and
returns one of three decisions:

- `exclude` — a blocker fired. Do not spend effort. Final.
- `ask` — something needs a fact only the candidate has.
- `apply` — proceed.

Coverage (how much of the posting's must-haves the CV evidences) is a heuristic:
token overlap over requirement sentences. It is a filter, not a verdict, and the
output says so.

## 3. Outreach is a letter, not a broadcast

Writing to a hiring team is normal and effective. Blasting is not, and it costs
a domain's reputation permanently.

Rules enforced in `outreach.py`:

- **No guessed addresses.** Only addresses actually found in the posting,
  the company site, or a page we read. Pattern-guessed addresses are recorded as
  `unverified` and cannot be sent to without an explicit flag.
- **Dry run by default.** `send` requires `--send` *and* configured SMTP.
- **Per-domain cap** and a per-run cap, both from config.
- **One recipient per message.** No CC, no BCC.
- **An opt-out line** in every draft.
- **No attachments** on first contact.

Drafts are plain text, reference one specific detail from the posting, and ask
for one thing. A draft that cannot name a concrete reason for writing should not
be sent, so `draft` refuses when it cannot find one.

## 4. Discovery reads boards, not aggregators

Aggregators are the obvious first stop and the wrong one. They are stale by a
day or two, they hide the employer's own wording, and they charge for API access
that returns less than the employer already publishes.

So `sources.py` talks to the board itself. Every ATS on the list exposes the same
JSON its own careers page loads: Greenhouse, Lever, Workable, Ashby, Workday and
SEEK. That gives the full description, the real location string, the posting
date and a URL that will still work next week.

Three consequences, all deliberate:

- **It is a registry, not a crawler.** `firms.toml` names each employer and its
  board. Nothing is discovered by crawling a search engine, so nothing arrives
  from a company nobody chose.
- **Unplaceable is rejected.** A location that does not name a UK or Australian
  place is dropped rather than guessed at. "New York" contains "York"; the
  matcher splits multi-city strings and checks a non-UK list first, because the
  alternative is quietly offering US roles as London ones.
- **An unreadable posting is `ask`, not `apply`.** SEEK gives a teaser and puts
  the full page behind a bot wall. Screening a teaser is not screening, so those
  entries are marked for a human and linked.

## 5. LinkedIn and Indeed are read by hand, not by robot

Both are the obvious coverage gap and both are closed to this kind of tool in
different ways: Indeed answers 403 to anything that is not a browser, and
LinkedIn's job-search HTML answers 200 but rate-limits rapidly and forbids
collection in its terms. Their APIs are partner-only.

The useful position is not "find a way around it". It is to let the reader do the
one thing a robot cannot: open the page as themselves, save it, and hand over the
file.

`import` parses three shapes — schema.org `JobPosting` blocks, LinkedIn's
`base-search-card` markup, and Indeed's `jobTitle` markup. Verified against
LinkedIn's public search page: 60 cards, title, company, location and URL. An
imported card carries no description, so it enters the store as `ask` rather than
pretending a title was screened.

## 6. How an application actually gets completed

Discovery and screening are solved. Submitting is the remaining piece, and it is
the one where a wrong answer is expensive, so the design is deliberately
half-automatic.

The loop, per job:

1. **Pick the CV.** Three variants × two markets. The rule is the job's own
   words: fixed income and fund research → asset management; risk, VaR,
   performance → risk; credit and fixed income desk → credit risk. Market
   follows the posting's country. The choice is written to the application row,
   so the record says what was sent, not what was intended.
2. **Open the employer's form in a real browser.** Playwright, visible by
   default, because a silent submission is not something to run unattended.
   The ATS is identified from the URL and the vendored AIHawk plugin for that
   ATS supplies the selectors.
3. **Fill from the answer bank, never from the posting.** `[answers]` in
   config is the only source for what goes into a form. Postings and pages are
   untrusted input; a job advert cannot tell the filler what to write.
4. **Stop at the review screen.** The tool fills, uploads the CV, and then
   halts with a screenshot and a list of anything it could not answer. A human
   presses submit. `--submit` exists for the day this is trusted, and is off.
5. **Record it.** Append to `applications` with a proof screenshot path, the
   timestamp, and the variant used. The ledger is the only evidence a
   submission happened.

What is never automated: assessments, demographic questions, legal
attestations, and any question with no answer in the bank. Workday needs an
account per employer — those credentials go in the Windows Credential Manager,
never in the database.

The first slice, in order of value:

- `apply --prepare` — choose the variant, write the answer sheet, open the
  posting, record the intent. No form interaction, useful immediately.
- Greenhouse and Lever forms — the two simplest ATSs, and the vendored fixtures
  let every selector be tested without submitting anything.
- Workday last, because account creation and multi-step wizards are where it
  breaks.

## Assessments

Timed and proctored assessments are not attempted. Platforms fingerprint
keystrokes, record the screen, and check submissions for machine authorship;
detection means a rescinded offer and a permanent flag on that employer's ATS.

What is built instead: practice in the real format, a question bank per company,
and scaffolding for take-home work that leaves the reasoning with the candidate.

## Roadmap

1. ~~Screening, store, outreach skeleton~~ — done.
2. ~~Discovery: direct board endpoints, location and title filters, dedupe~~ —
   done for Greenhouse, Lever, Workable, Ashby, Workday and SEEK.
3. ~~Dashboard over the store~~ — done: jobs, applications, assessments,
   interviews, outreach, CV library.
4. Form filling: Playwright + the vendored plugins, tested against the vendored
   fixtures, review-before-submit.
5. Answer bank UI, with the credential store for Workday.
6. Assessment prep: timed drills, company question banks.
7. More sources: Oracle HCM (Schroders, AustralianSuper, JPMorgan, AMP) and
   SmartRecruiters (Evelyn Partners) were detected but are not implemented yet;
   Adzuna needs a free key and would add UK aggregator coverage.
