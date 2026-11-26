# Handoff prompt — JobHunter

You are taking over a private, working-but-incomplete job-application system. Your
mission is to make it produce **many truthful applications with minimal human
time**, and to help its owner land a job. Read this whole file before changing
anything; it is the map, the rules, and the state you are inheriting.

---

## 0. Start here

- **Repo:** `C:\Users\jedda\dev\jobhunter` (private, GitHub `DejAmour/JobHunter`, branch `main`).
- **Owner:** Jed Dahlke, MSc Mathematical Finance (Exeter), based in Exeter, UK;
  targets UK (all regions except Birmingham) and Australia (citizenship by descent).
- **Platform:** Windows, Python 3.13, Playwright/Chromium installed.
- **Run it:**

  ```powershell
  cd C:\Users\jedda\dev\jobhunter
  $env:PYTHONPATH="src"
  python -m jobhunter serve --port 8765      # dashboard: http://127.0.0.1:8765
  python -m pytest -q                        # ~5.5 min, 230 tests
  ```

- **Binding decisions:** `docs/design.md` (ADR 1–10). They are not suggestions;
  if you want to violate one, say why in the file and in your report.
- **Never** print, log, commit or paste a password, an API key, or the contents of
  `~/.jobhunter/accounts.toml`, `config.toml`, `facts.toml`, `jobs.db`, `sessions/`.

---

## 1. What exists today

A pipeline, not a scraper:

```
discover → screen → prepare pack → fill the employer's form → human review → submit → track
```

| module | job |
|---|---|
| `sources.py` | board adapters: Greenhouse, Lever, Workable, Ashby, Workday, Oracle HCM, SEEK, SmartRecruiters, Adzuna, Reed, Indeed, JobSpy |
| `discover.py` | runs the boards, filters by title/location, screens, dedupes by fingerprint, stores |
| `screening.py` | knockout extraction + graded CV-coverage heuristic (reported as heuristic) |
| `apply.py` | picks the CV variant and market, writes `output/applications/<slug>/application.md` |
| `fill.py` | Playwright fillers: `Ats` base class, `Login`, `FillReport`, question engine |
| `questions.py` | rules that answer a form question from the answer bank; protected list |
| `choices.py` | bounded option resolver (exact → prefix → synonym → remembered per tenant) |
| `runs.py` | background runs for the dashboard: fill and discovery, with progress and a held browser |
| `resolve.py` | follows an aggregator's Apply link; names where the application really happens |
| `record.py` | records a live form as a replayable fixture + control inventory (ADR 5) |
| `evidence.py` | traces: network, console, DOM, screenshot, summary, secrets masked |
| `accounts.py`, `credentials.py`, `sessions.py`, `mailbox.py` | employer accounts, Windows Credential Manager, saved sessions, verification codes |
| `web.py` + `templates/` | the dashboard: overview, postings, applications, outreach, CVs, one job page with Apply |

**The dashboard is the interface.** The owner does not want to use a terminal for
applying. A run is started from the job page, polled while it works, and can hold
a headed browser open until the human presses Done.

**Current numbers (26 Nov 2026):** 412 postings, 185 marked `apply`, 224 `ask`,
11 applications tracked, **1 really submitted** (Neudata, 24 Nov). Everything
else is a draft. Finding jobs is solved; converting them is not.

---

## 2. Rules that do not bend

1. **Never invent experience.** No Bloomberg (he has not used it), no issuer-level
   credit analysis, no professional VaR/ES measurement, no language or tool he
   does not have. If the advert asks for it, the pack says the CV does not
   evidence it.
2. **The answer bank is the only source of facts.** A field with no answer stays
   empty and is reported. An empty field that fails the employer's validation is
   the correct outcome.
3. **Protected questions are never auto-answered** (gender, ethnicity, veteran,
   disability, religion, DOB, criminal history). An explicitly stored disclosure
   may be used; nothing may be inferred.
4. **Submit is off by default**, and `submitted` means *the page moved*, never
   that a button was clicked.
5. **No CAPTCHA solving, no proctored/timed assessment solving.** Those are out of
   scope by policy, not by difficulty.
6. **A run must end in a named outcome**: `submitted`, `filled with these tasks
   left for you`, `closed`, `needs an account on X`, `blocked by X`. "Failed" with
   no reason is a bug.
7. **Personal data never enters git.** CVs, contact details, the store, sessions
   and traces are gitignored by design.

---

## 3. What is proven, and what is not

Proven against live tenants (each has a trace under `~/.jobhunter/traces/`):

- Discovery from all 12 adapters; a full pass over 34 firms finds ~130 new
  postings in ~15 minutes.
- Fillers: Greenhouse (current react-select form), Lever, Workable, Ashby,
  Workday's wizard including M&G's account wall and its ten knockout questions.
- One real submission (Neudata, Ashby) through the dashboard's held browser.
- Recorded fixtures replayed in tests: Workable and Ashby.

Not proven / not built:

- Workday tenants other than M&G (the filler generalises, the evidence does not).
- Any Oracle HCM application form (discovery works).
- Any account-gated board application: Reed, Indeed, LinkedIn, Totaljobs.
- SmartRecruiters application (DataDome challenge).
- Cover letters: there is no generator; some forms require a file.

---

## 4. The failure the owner hit last — diagnose before you code

He clicked Fill on an Adzuna-sourced job. The run said:

```
the application happens on www.adzuna.co.uk, which has no filler yet:
https://www.adzuna.co.uk/jobs/land/ad/5977566092?aztt=...
```

Reproduced on 26 Nov: `https://www.adzuna.co.uk/jobs/land/ad/<id>` returns **HTTP
403 (CloudFront "Request blocked")** to the Playwright browser on this machine.
The same Adzuna pages load fine in the human's desktop browser. So:

- **118 of the 185 `apply` rows are aggregator links**, and the automated browser
  is blocked by the aggregator's CDN while the human's browser is not.
- This is the single largest reason "it did not work". Any plan that ignores it
  will keep producing 403s.

Direction: **use the human's browser for the hand-off** (the harness has an in-app
browser tool that shares the desktop session), or resolve the advert to the
employer/agency posting and never present an aggregator listing as fillable.
Both are acceptable; a third vague error is not.

Other known walls, already diagnosed:

| failure | diagnosis | fix direction |
|---|---|---|
| Reed / Indeed / LinkedIn | the Apply button opens the board's own account-gated application, no outbound link | store a session per board, record the flow once, fill it |
| SmartRecruiters apply | DataDome CAPTCHA | needs one human-solved recording; do not automate around it |
| 10 of 32 Workday boards | HTTP 429 while crawling | defer and retry at the end of a run with backoff; never treat 429 as "no jobs" |
| Oracle HCM | discovery only | add an `Ats` subclass; the apply URL is the requisition page |
| Greenhouse essay questions | no rule, correctly refused | a draft-from-bank tool with mandatory human edit |

---

## 5. Where the leverage is

`185 apply` → `11 tracked` → `1 sent`. Discovery is no longer the bottleneck; the
human's time per application is. Optimise for **completed applications per hour
of the owner's attention**, not for more postings.

The owner wants: open a job → one button → the form filled → finish 1–3 things and
submit → tracked. If a job cannot be automated, the page must say why in one
sentence and offer the manual route.

---

## 6. Priorities, with acceptance criteria

**P0 — this week**

1. **No more "no filler".** Every URL is classified before a run starts: known
   ATS / aggregator / account board / closed / unknown. The UI shows the class and
   what the button will do. Acceptance: clicking Fill never produces an
   unclassified failure.
2. **Aggregator hand-off that works.** Use the desktop browser (or a "paste the
   destination URL" box) to get from an Adzuna/Reed listing to the real form.
   Acceptance: 3 different aggregator-sourced jobs reach a real form and are
   either filled or named as account-gated.
3. **Coverage report.** A page listing every `apply` job with: can be filled now /
   needs an account on X / aggregator-only / closed, and the last attempt. This
   replaces guesswork about "only 4 listings".
4. **Runs survive a page reload.** Run state is in memory; persist it (SQLite is
   fine) so the Done button and progress survive a restart or a closed tab.

**P1**

5. Reed and Indeed: session + recorded flow + filler. These two boards alone cover
   a large share of the aggregator rows.
6. Workday: deferred retry for 429s; prove a second tenant end to end (abrdn,
   Quilter, Wellington).
7. Oracle apply filler (Schroders, JPMorgan, Lazard, AMP).
8. Cover-letter drafting from the answer bank, always flagged for human edit.

**P2**

9. SmartRecruiters, after a human-solved recording.
10. The post-application half: follow-up drafts after N days, interview notes,
    stage transitions from email.

---

## 7. Definition of done

- **20 truthful applications a week**, each costing the owner under 5 minutes.
- Zero vague failures; every run names its outcome and what to do next.
- Every statement on a form traceable to `config.toml` / `facts.toml`.
- At least 10 real submissions logged with proof before any further platform work.
- The system learns from outcomes: rejections and interviews feed back into which
  roles and variants get used.

---

## 8. How to work

- **Evidence first.** Reproduce, read the trace (`~/.jobhunter/traces/<run>/summary.txt`),
  then fix. Write the failing test before the fix.
- **Record real DOM before writing selectors** (ADR 5). `jobhunter record --job N`
  saves the page and an inventory of its controls; write the filler from that.
- **Small commits**, full suite green, one paragraph explaining the change.
- **Report in the owner's terms**: what now works, what is still his to do, what
  needs him (an account, a decision, a password).
- **Ask before anything irreversible** (submitting an application, changing a
  password, registering an account).
- Do not add an agent that writes free text or presses submit on its own. The
  value of this project is that it is honest and reviewable.

---

## 9. First 48 hours

1. Read `docs/design.md`; run the suite; open the dashboard; click through one
   job and one application.
2. Reproduce the Adzuna 403 with your own trace. Implement the browser-side
   hand-off and prove it on three jobs.
3. Build the coverage report; use it to tell the owner exactly which of the 185
   `apply` rows are actionable today.
4. Get five real applications submitted with him, one per evening, and record
   where each one cost him time.
5. Only then start P1 — and tell him which P1 item you chose and why.

Questions to ask him on day one: which job boards does he already have accounts
on? Does he want the tool ever to press submit unassisted (default: no)? Which of
the 185 `apply` rows does he actually want to pursue first?
