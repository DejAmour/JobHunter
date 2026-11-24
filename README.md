# jobhunter

A job application assistant that finds roles, checks whether they are worth
applying to, fills the employer's own form, and writes to hiring teams.

Built by taking the best parts of two existing open-source projects rather than
starting from nothing:

- **AIHawk** (`feder-cr/Jobs_Applier_AI_Agent`, MIT) — the per-site Markdown
  plugin format, the title-graph search plan, and proof screenshots per send.
- **JobAppAgent** (`vaibhavarora14/job-application-agent`, MIT) — the OS
  credential store, HTML fixtures of real ATS forms, the answer bank, the
  knockout/duplicate gates, and the append-only ledger.

Vendored copies live in `vendor/`, with their licences. Nothing is copied
without attribution; see `docs/design.md`.

## What it does not do

It does not sit employer assessments, timed tests, or proctored screens, and it
does not answer demographic or legal-attestation questions. Both upstream
projects draw the same line independently. See "Assessments" in
`docs/design.md`.

## Status

Working today:

- **Discovery** — reads employer job boards (Greenhouse, Lever, Workable,
  Ashby, Workday, Oracle HCM, SEEK, SmartRecruiters) and, for coverage, Adzuna,
  Reed, Indeed and JobSpy. Filters by title and location, screens what survives, and
  stores it.
- **Dashboard** — a local web app tracking jobs, applications, assessments,
  interviews, outreach and CV versions.
- **Form filling** — Playwright against the employer's own form: Greenhouse,
  Lever, Workable, Ashby, and Workday's wizard including its account wall. It
  fills from the answer bank, uploads the CV and stops at Review. It does not
  submit.
- **Recorded fixtures** — `jobhunter record` opens a live form and saves its
  markup, so a filler is written against the tenant's page rather than one we
  wrote. The Workable and Ashby fillers were built this way.
- **Traces** — every browser run writes `~/.jobhunter/traces/<run>/`: network
  log with secrets masked, console, DOM, screenshot, and a `summary.txt` naming
  every submission and its response.
- CV parsing and audit, screening (knockout extraction), the SQLite store,
  CV generation from `cv/content.toml`, and outreach drafting with a dry-run
  sender.

Not yet: SmartRecruiters form filling (its apply page is served behind a
DataDome challenge, so the markup has to be recorded from a signed-in browser
once); an answer-bank UI (the fact sheet is edited with `jobhunter facts`).

## Accounts and passwords

Employer accounts are per tenant: the same email and password at M&G will not
work at abrdn. The email lives in `~/.jobhunter/accounts.toml` (gitignored); the
password lives in the Windows Credential Manager and never in a file.

```
jobhunter account add --firm "M&G" --generate   # generate, store, show once
jobhunter account show --firm "M&G"             # print it when you must type it
jobhunter account export                        # CSV any manager can import
```

Generated passwords satisfy the rules the form prints, and avoid lookalike
characters, because an account that locks sends you back to a reset form with a
text field. The export is plaintext on purpose: import it into Bitwarden or
KeePassXC, then delete it. The tool keeps using the credential store either way.

### If you use Google Password Manager

Chrome will offer to save a generated password, and that is fine — but nothing
can read it back. There is no API for it, and automating a browser's own
credential store would mean defeating a security boundary, so this tool does not
try. Two supported bridges instead:

```
# you let Chrome generate it, then hand that one over
jobhunter account import --csv passwords.csv --firm "M&G"

# or this generates it, and you save it in Chrome the first time you sign in
jobhunter account add --firm "M&G" --generate
```

For the first: Google Password Manager → Settings → Export passwords. That file
contains **every** password you have saved, so the importer takes one employer at
a time and refuses when it cannot identify the row; then delete the file.

### When an account locks

Workday locks an account after repeated failed sign-ins, so the tool stops at the
first lock rather than trying the other door — retrying is what turns a reset
into a support ticket.

```
jobhunter account recover --firm "M&G"
```

It opens the tenant, presses *Forgot your password*, waits for the reset mail,
opens the link in the same window, and prints a generated password that meets the
form's printed rules. You paste it twice; it then proves the password by signing
in with it, and stores it only if that succeeds. Filling the reset form itself is
not automated yet: those selectors have to be recorded from a real page first,
which is the same discipline the account wall needed.

## Reading your inbox

Verification codes — and password-reset links when an account locks — arrive by
email. `jobhunter mail setup` stores a Gmail *app password*
(myaccount.google.com/apppasswords) in the same credential store.

```
jobhunter mail check                        # can this machine read the inbox?
jobhunter mail link --firm "M&G" --open     # newest reset or verification link
```

It reads headers, the first text part and anchor hrefs, and never marks a
message read. Without it nothing touches your mail: the browser waits for you to
type the code or click the link yourself.

## Discovery

```
python -m jobhunter discover               # every registered employer
python -m jobhunter discover --ats workday # one board type
python -m jobhunter discover --firm "M&G"  # one employer
python -m jobhunter discover --dry-run     # fetch and filter, write nothing
```

Employers live in `firms.toml`. Adding one is a probe, not a guess:

```
python -m jobhunter probe --domain rathbones.com
python -m jobhunter probe --file domains.txt --write
```

`probe` reads the employer's careers page, works out which applicant tracking
system it loads, calls that system's API, and only writes the entry if jobs came
back. A 404 guess and an employer with no public board look identical, which is
why guessing slugs is not how the registry grows.

Boards read directly: Greenhouse, Lever, Workable, Ashby, Workday, Oracle HCM,
SmartRecruiters, SEEK.

Indeed and its siblings: JobSpy (`pip install python-jobspy`), which also
resolves the employer's own URL behind each advert.

Aggregators: Adzuna (UK and Australia) and Reed (UK). Both need a free key —
see `[aggregators]` in `~/.jobhunter/config.toml`. They add employers with no
public board at the cost of stale copies and recruiter adverts, which is why they
sit alongside the boards rather than instead of them.

Filters, in order: title vocabulary → country (UK or Australia, Birmingham
excluded, unplaceable rejected) → excluded companies → screening. A posting
whose listing text is too short to screen is marked `ask`, not `apply`, because
an unreadable posting is not an approving one.

To keep the list current without thinking about it:

```
powershell -ExecutionPolicy Bypass -File scripts\schedule_discovery.ps1
```

## Reading the full posting

Aggregators truncate: Adzuna gives 500 characters, Reed about 450, SEEK one line.
That is enough to spot a title, not enough to screen, so those rows land at
`apply` with no coverage.

```
python -m jobhunter refresh --limit 40
python -m jobhunter refresh --country uk --decision apply
```

It opens each posting in a real browser (many are JavaScript applications),
takes the main text, re-screens it and rewrites the row. A posting that still
cannot be read is marked `ask` — an unreadable posting is not an approved one.

## Preparing an application

```
python -m jobhunter apply --job 2
python -m jobhunter apply --job 2 --open
```

This is not a form filler; it is the part that is tedious but safe. It picks the
CV variant the posting's title points at, writes an answer sheet from
`[answers]`, pulls the CV lines that overlap most with the advert, lists what the
posting asks for that the CV does not evidence, records the intent, and leaves a
checklist in `output/applications/<company>-<role>/application.md`.

Nothing is submitted and nothing is invented.

## Filling the form

```
python -m jobhunter fill --job 2            # fills, screenshots, stops
python -m jobhunter fill --job 2 --submit   # presses submit
```

Greenhouse, Lever, Workable and Ashby are done. The filler opens the posting in a
real browser, writes the values from `[answers]`, uploads the CV the pack chose,
screenshots the result into the application folder, and stops at the review
screen. Workable and Ashby put the form behind an "Apply" control on the posting,
so the filler follows that first.

Rules it will not break:

- **The answer bank is the only source.** A posting cannot tell the filler what
  to write.
- **A field with no answer stays empty and is reported.** An empty required field
  fails at the employer's own validation, which is the right place to fail.
- **A date is never guessed.** A date picker is reported for you to fill; a
  sentence like "Available immediately" is not a date.
- **Submit is off** unless you pass `--submit`.

Proved against recorded fixtures — real Greenhouse and Lever markup from the
vendored JobAppAgent set, and Workable and Ashby pages captured from the live
tenants by `jobhunter record` — so the tests exercise the selectors without
touching an employer:

```
python -m pytest tests/test_fill.py tests/test_recorded_forms.py -q
```

### Recording a form you have not written a filler for

```
jobhunter record --job 274 --headless
jobhunter record --url "https://apply.workable.com/acme/j/ABC123" --headed
```

It opens the page, presses the obvious Apply control if the form is behind one,
and writes the markup to `tests/fixtures/recorded/<ats>/` with a `.json` listing
every control the page really had — tag, type, name, id, label, options. Scripts
and remote assets are stripped so the fixture replays offline. A page with no
form controls (a captcha, a closed posting) is kept under `_failed/` as evidence
and is never replayed.

The recorder is the answer to a specific failure mode: a fixture written by hand
passes while the live page fails. Write the selectors from the inventory, not
from memory.

### Workday

Workday is five pages behind an account: My Information, My Experience,
Application Questions, Voluntary Disclosures, Review. The filler clicks Apply,
signs in, fills whichever step is showing, and with `--advance` walks to Review.

Register the employer once:

```
jobhunter account add --firm "M&G"
```

It asks for the email and the password twice. The email goes to
`~/.jobhunter/accounts.toml`; the password goes to the Windows Credential
Manager. Neither is committed, and nothing else ever reads the password.

Then the same command as everywhere else:

```
jobhunter fill --job 4 --advance
```

The first run at an employer creates the account when a verification code can be
read (see `jobhunter mail setup`), and saves the signed-in session. Every
application after that at the same employer skips the sign-in. To watch the
sign-in happen instead, `jobhunter login --site workday --firm "M&G"` opens a real
window and waits for you.

It still does not submit. `--submit` is a separate decision.


### The fact sheet

```
python -m jobhunter facts
```

Asked once, reused by every form: name, address, postcode, phone, quals, notice
period, salary expectation, right to work. Stored in `~/.jobhunter/facts.toml`,
which is gitignored, and read by every command.

Voluntary disclosures (gender, ethnicity, veteran status, disability) are the
last four questions and are **opt-in**: say no and they are never stored, and
forms leave them blank. Say yes and they are filled - still flagged for review,
because a declaration about you is always yours to confirm.

The answers can be edited by hand instead; the question list is
`src/jobhunter/facts.py`, the answers are `~/.jobhunter/facts.toml`, and pressing
Enter keeps whatever is in brackets.

### Verification codes

Creating a Workday account sends a one-time code. To have it read rather than
typed:

```
jobhunter mail setup
```

That wants a Gmail **app password** (myaccount.google.com/apppasswords, which
needs 2-step verification) - not your account password. It is stored in the
Credential Manager like everything else. Skip it and the browser simply waits for
you to type the code.


### Questions on the form

Two kinds, treated differently.

**Facts** are transcribed from the answer bank: sponsorship on a UK role is `No`,
right to work is `Yes`, notice period, current title, qualifications, how you
heard. If a question names a country you are not entitled to work in, it is not
answered at all — a form asking "are you authorised to work in the United
States?" gets no answer from a UK applicant, because saying "Yes" there is a
false statement on a legal declaration.

**Words** — "why this role", "your strengths", "career goals" — come from
`[answers.free_text]` in config, with `{company}`, `{role}` and `{evidence}`
substituted. They are flagged for review even when filled: a paragraph you wrote
still deserves a read before it goes to an employer. `why_company` is deliberately
empty, because a generic "why us" is the answer that reads as fake and has to be
written per firm.

Never answered, whatever the bank says: gender, ethnicity, veteran status,
disability, religion, date of birth, criminal history. Those are declarations
about a person, and a blank field the employer rejects is a better outcome than a
confident guess.

## LinkedIn and Indeed

Indeed is read through **JobSpy** (`speedyapply/JobSpy`, MIT), used as a library:

```
python -m jobhunter discover --ats jobspy
```

It reaches Indeed, fetches full descriptions, and resolves `job_url_direct` —
the employer's own posting behind the advert. 60 of the first 67 postings came
back with the employer URL rather than an Indeed redirect, which also means a
role found here and later seen on a Workday board deduplicates correctly.

No login is needed for search. If a step ever needs you signed in (Indeed Easy
Apply, Workday accounts), capture a session once:

```
python -m jobhunter login --site indeed
```

That opens a browser window, you sign in yourself, and it saves the resulting
cookies to `~/.jobhunter/sessions/indeed.json`. The tool never sees or stores a
password.

There is also a Playwright reader for Indeed (`ats = "indeed"`, off by default).
It works but trips verification challenges after a handful of pages, so JobSpy is
preferred. LinkedIn has no working route: its APIs are partner-only and its
public pages rate-limit and forbid collection. Save a LinkedIn page and import
it instead:

```
python -m jobhunter import --file "LinkedIn jobs.html"
```

Checked against LinkedIn's public search page: 60 cards read, each with title,
company, location and URL. Nothing is fetched, no login is used.

## Dashboard

```
python -m jobhunter serve        # http://127.0.0.1:8765
```

Local only, bound to the loopback address, no accounts. It reads the same
SQLite file the CLI writes, so anything discovered is visible immediately, and
it is safe to leave running while a discovery run writes.

<!-- CV BUILD NOTE -->

## The CV is generated

Don't edit the DOCX. Edit `cv/content.toml` and run:

```
python scripts/build_cv.py --all
```

Contact details live in `cv/contact.toml`, which is gitignored — a public
repository should not carry a phone number. On a fresh clone:

```
copy cv\contact.example.toml cv\contact.toml
```

then fill in your own name, location, phone and email. The build overlays that
file on top of `content.toml`, so the work history stays in one reviewable place
and the personal details stay out of git.

That writes one DOCX per variant per market — `Jed Dahlke CV Risk.docx`,
`Jed Dahlke CV Risk (AU).docx`, and so on — plus a `.txt` twin. Convert with
`python scripts/export_pdfs.py`, then check any version with:

```
python -m jobhunter cv-audit --file "output/Jed Dahlke CV Risk.pdf"
```

Variants inherit the master (`base = "../content.toml"`) and override only the
profile line and skills groups, so three CVs cannot drift into three sets of
facts. Work rights are per market: the UK files carry no work-rights line, the
Australian files claim eligibility for citizenship by descent.

## Layout

```
src/jobhunter/     code
  sources.py       one adapter per job board; public JSON endpoints only
  discover.py      title / location / company filters, then screening
  screening.py     reads a posting, returns blockers / flags / coverage
  outreach.py      finds addresses, drafts, sends (dry run by default)
  web.py           the local dashboard
  templates/       server-rendered pages, no build step
  db.py            SQLite store (WAL, so reads and writes do not block)
  cli.py           command line
firms.toml         the employer registry
cv/content.toml    the CV, single source of truth
cv/variants/       risk and credit-risk framings that inherit the master
vendor/aihawk/     MIT, plugins/*.md — one per ATS
vendor/jobappagent/fixtures/   MIT, real form HTML for offline tests
tests/             pytest
```

## Quick start

```
python -m jobhunter doctor
python -m jobhunter db-init
python -m jobhunter discover
python -m jobhunter serve
```

`doctor` reports what is configured and what is missing. Nothing sends email
unless `--send` is passed and SMTP is configured.
