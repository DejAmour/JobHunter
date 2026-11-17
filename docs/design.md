# Design

Three decisions shaped this. Each is written down with the reason, so it can be
argued with later rather than rediscovered.

## 1. Adopt, do not rebuild

Two projects already solve the expensive part — driving a real browser through
a dozen applicant-tracking systems. Rebuilding that is months of work to reach
the same place. The split:

| Taken from | What | Why that one |
|---|---|---|
| AIHawk | Title-graph search plan | Deterministic keyword scoring is cheap and inspectable; only the top ~100 postings go to a model. |
| AIHawk | Screenshots + `data/trace.log` per send | Proof of what was submitted, and a way to debug a form after the fact. |
| JobAppAgent | OS credential store | Workday needs a per-company password. Storing it in a local DB is the one thing that would make a leak catastrophic. Use Windows Credential Manager / DPAPI, never a column. |
| JobAppAgent | `vendor/jobappagent/fixtures/` | Real Greenhouse/Ashby/Lever/Workday HTML. Lets every selector be tested without submitting to an employer. |
| JobAppAgent | Answer bank | A stable set of facts about the candidate. Postings and forms are untrusted input; the answer bank is the only source of truth for what gets written into a form. |
| JobAppAgent | Duplicate, requisition and cooldown gates | Re-applying to the same req is the most common way to get filtered. |

### The prose-plugin idea, and why it was dropped

The original plan was to take AIHawk's `plugins/*.md`: a Markdown file per ATS
naming selectors and failure modes, which the model then adapts at run time.
That is cheap to write and impossible to test — there is no fixed behaviour to
assert, so a regression can only be found by running against a live tenant.

What exists instead is `fill.py`: ordinary Python with hard-coded
`data-automation-id` values, and a test per page shape. Slower to write, but a
failure is reproducible offline and a change can be reviewed before it is run
against an employer. The vendored Markdown is kept as reference material, not
as executable configuration. (ADR 1.)


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

## 4. Discovery prefers boards; aggregators are a supplement

Aggregators are the obvious first stop and the wrong *first* one. They are stale
by a day or two, they hide the employer's own wording, and they charge for API
access that returns less than the employer already publishes.

So `sources.py` talks to the board itself. Every ATS on the list exposes the same
JSON its own careers page loads: Greenhouse, Lever, Workable, Ashby, Workday and
SEEK. That gives the full description, the real location string, the posting
date and a URL that will still work next week.

Aggregators were added afterwards, for coverage rather than quality: Adzuna and
Reed where a key or a public endpoint exists, Indeed through a real browser
because it answers 403 to anything else, JobSpy as a last resort. They are
marked as aggregators in the store, so the dashboard can show direct employers
only. (ADR 2.)

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
   The ATS is identified from the URL and the matching filler in `fill.py`
   supplies the selectors. Those selectors are Python, and each page shape has
   a test; the vendored Markdown plugins are reference only (ADR 1).
3. **Fill from the answer bank, never from the posting.** `[answers]` in
   config is the only source for what goes into a form. Postings and pages are
   untrusted input; a job advert cannot tell the filler what to write.
4. **Stop at the review screen.** The tool fills, uploads the CV, and then
   halts with a screenshot and a list of anything it could not answer. A human
   presses submit. `--submit` exists for the day this is trusted, and is off.
5. **Record it.** Append to `applications` with a proof screenshot path, the
   timestamp, and the variant used. The ledger is the only evidence a
   submission happened, so `submitted` means the page moved — not that a click
   was dispatched.

## 7. Every run leaves evidence

A screenshot shows what the page looked like. It cannot show whether a request
left the browser or what the server answered, and those are the only facts that
separate "the click never landed" from "the server rejected the values".

So `evidence.py` writes a folder per run under `~/.jobhunter/traces/`: the
network log (request bodies included, secrets masked on the way in), console
messages, page errors, the DOM, a screenshot, and a `summary.txt` that names
every submission and what came back. The first line of a diagnosis is
"submissions: 0" or "POST /register -> 400".

This exists because a Workday account wall failed silently: fields filled, no
error, page unmoved. Six hand-written tests passed while the live run failed,
and the only artefact was a picture of a form. (ADR 3.)

Tracing is not a substitute for tests. It is what makes a live failure
diagnosable in one attempt rather than five.

What is never automated: assessments, demographic questions, legal
attestations, and any question with no answer in the bank. Workday needs an
account per employer — those credentials go in the Windows Credential Manager,
never in the database.

The first slice, in order of value:

- ~~`apply --prepare`~~ — done: variant, answer sheet, evidence, gaps, intent.
- ~~Greenhouse and Lever forms~~ — done, tested against the vendored fixtures,
  stopping at the review screen.
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
4. ~~Form filling: Playwright, tested against the vendored fixtures,
   review-before-submit.~~ Done for Greenhouse and Lever, and for Workday's
   wizard. Workday's account wall is the open problem; Ashby, Workable and
   SmartRecruiters are next (ADR 4).
5. ~~Credential store for Workday.~~ Done. Answer bank UI still pending; the
   fact sheet is edited through `jobhunter facts` for now.
6. Assessment prep: timed drills, company question banks.
7. More sources: Oracle HCM (Schroders, AustralianSuper, JPMorgan, AMP) is
   detected but not implemented. SmartRecruiters, Adzuna and Reed are done;
   Indeed runs through a browser and JobSpy is the last resort.

## Decision records

Short, dated, and reversible. Each one is here because the alternative was
tried or considered and the reason it lost would otherwise be rediscovered.

- **ADR 1 — Selectors are code, not prose.** AIHawk's Markdown plugins were the
  plan; they cannot be tested, so a regression only shows up live. Replaced by
  hard-coded selectors plus fixtures per page shape.
- **ADR 2 — Boards first, aggregators second.** Aggregators were originally
  excluded on quality grounds. Coverage won; they are tagged so direct
  employers can be filtered, and a README claim that discovery is board-only
  was removed.
- **ADR 3 — Every browser run is traced.** A silent Workday failure left only a
  screenshot, which cannot distinguish a lost click from a server refusal.
  `evidence.py` records requests (secrets masked), console, DOM and a summary.
- **ADR 4 — The apply side gets a contract.** `sources.py` has a `Source` base
  class and a registry; `fill.py` has neither, and `run()` branches on the
  string `"workday"`. The next ATS is a subclass, not a fourth copy of the
  same six loops.
- **ADR 5 — Recorded fixtures beat hand-written ones.** Tests built from markup
  we wrote pass while the live page fails. Behaviour is recorded from the real
  tenant once and replayed.
- **ADR 6 — One credential store, with an exit to a real manager.** Passwords
  live in the OS store because that is the only backend an unattended run can
  read without an unlock prompt. Generated passwords are the default for new
  accounts; `account export` writes a plaintext CSV for Bitwarden or KeePassXC
  so the human copy is a real manager, not a notes file. Mail access exists only
  to read a code or a reset link, and never marks a message read.
- **ADR 7 — Recovery is assisted, not automatic.** The full loop (forgot
  password → open the email link → set a new password → sign in) is mechanically
  buildable, and rejected as the default. It runs once per employer at most, it
  is the single most bot-like thing the tool could do, and every unmapped form
  costs a live attempt. So `account recover` does the tedious parts — find the
  link, generate a compliant password, verify it by signing in, store it only on
  success — and leaves two keystrokes and one submit to the human. Automate the
  last mile only after recording the real reset form.
- **ADR 8 — A lock is terminal for the run.** Repeated sign-in attempts are what
  cause locks, so the tool stops at the first "account locked" and says so,
  rather than trying the other door.
- **ADR 9 — A bounded resolver, not an agent.** Choosing an option is the one
  part of a form that is not deterministic: the bank says "Company website" and
  the tenant offers "Internet". `choices.choose` resolves that from a closed
  list — exact, then what this tenant chose last time, then a short preference
  list for questions where any close option is acceptable. It returns a string
  the form actually offered, or None. A model could sit behind the same
  signature, choosing from the same list; nothing else would change. What it
  will never do is type into a field or press a button.
