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

- **Discovery** — reads employer job boards directly (Greenhouse, Lever,
  Workable, Ashby, Workday, SEEK), filters by title and location, screens what
  survives, and stores it.
- **Dashboard** — a local web app tracking jobs, applications, assessments,
  interviews, outreach and CV versions.
- CV parsing and audit, screening (knockout extraction), the SQLite store,
  CV generation from `cv/content.toml`, and outreach drafting with a dry-run
  sender.

Form filling is designed but not yet implemented; the ATS plugins and fixtures
it will use are already vendored.

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

Boards read directly: Greenhouse, Lever, Workable, Ashby, Workday,
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

Greenhouse and Lever are done. The filler opens the posting in a real browser,
writes the values from `[answers]`, uploads the CV the pack chose, screenshots
the result into the application folder, and stops at the review screen.

Rules it will not break:

- **The answer bank is the only source.** A posting cannot tell the filler what
  to write.
- **A field with no answer stays empty and is reported.** An empty required field
  fails at the employer's own validation, which is the right place to fail.
- **Submit is off** unless you pass `--submit`.

Proved against the vendored fixtures, which are real Greenhouse and Lever
markup loaded from disk, so the tests exercise the selectors without touching an
employer:

```
python -m pytest tests/test_fill.py -q
```

### Workday

Workday is five pages behind an account: My Information, My Experience,
Application Questions, Voluntary Disclosures, Review. The filler detects which
step is showing, fills what it can, and with `--advance` walks to the next step,
stopping at Review.

```
python -m jobhunter fill --job 4 --advance
```

What it will not do: create an account or sign in for you. Workday accounts are
per employer, so that one is yours, and the password belongs in Windows
Credential Manager rather than in this repository.
### The fact sheet

```
python -m jobhunter facts
```

Asked once, reused by every form: name, address, postcode, phone, quals, notice
period, salary expectation, right to work. Stored in `~/.jobhunter/facts.toml`,
which is gitignored, and read by every command.

Voluntary disclosures (gender, ethnicity, veteran status, disability) are the
last four questions and are **opt-in**: say no and they are never stored, and
forms leave them blank. Say yes and they are filled — still flagged for review,
because a declaration about you is always yours to confirm.

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
