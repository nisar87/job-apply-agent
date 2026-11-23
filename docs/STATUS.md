# Build status

Last updated 23 November 2026. The version 2 plan is approved (`docs/MASTER_PRODUCT_SPEC_V2.md`,
`docs/IMPLEMENTATION_ROADMAP_V2.md`); work is on branch `v2-build`, reviewed as a pull request, and nothing
below has been deployed to the live site yet.

## Version 2, phase 0 (safety and truth), on branch v2-build

| Change | Effect |
| --- | --- |
| Employer addresses are no longer looked up on company websites | An application email goes only to an address printed in the job ad. A job that shows only a web form waits for form submission (phase 3). Removes the date-dependent `test_contacts` failure along with the lookup |
| Signed-out visitors | Internal pages render nothing, and start no requests, until a sign-in is present |
| Funnel per user | Admin page shows, for each user over 7 days: new jobs, checked, good matches, with an address in the ad, written, stopped, sent, target, and the step where the target was lost |
| Test or live is always visible | Every signed-in page on a non-production site shows a banner; it turns to a warning when the account is switched to real sending. `/api/auth/me` returns `environment` and `sends_for_real` |

## Version 2, phase 1 (matching that can be trusted) and phase 2 (models), on branch v2-build

| Change | Effect |
| --- | --- |
| Gates in code | Required languages, role family, level (within one step of the CV's), own-country exclusion, and right to work / sponsorship (EU free movement respected). A gate applies only when the user's side is known, so old accounts behave as before |
| Posting reading | Extra fields: required languages, role family, level including executive and intern, stricter sponsorship meaning |
| CV reading | Level, role families and working languages are derived from the CV and stored in the fact sheet; saving the fact sheet keeps them |
| Same job under another link | Not applied to twice (same employer, same title) |
| Claude provider | `app/llm/claude.py`; strong tasks on Claude when `ANTHROPIC_API_KEY` is set, Gemini for fast tasks and embeddings, automatic fallback in both directions, Claude paused 5 minutes after a key or credit failure. Tested with simulated models only; not yet run against the real Claude API |

## Version 2, phase 3 (form submission), on branch v2-build

| Change | Effect |
| --- | --- |
| Eight hiring systems | Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio, Teamtailor are recognised, each with its own confirmation wording |
| Full auto | In full-auto mode the agent submits on these systems without waiting; a generic form is never submitted automatically |
| Unconfirmed | If no confirmation shows after the final click, the application is recorded as "unconfirmed" with a screenshot, counted against limits, and never retried by itself |
| Email confirmation | A confirmation email naming the employer and role within 30 minutes turns "unconfirmed" into "sent" |
| Daily limit | 10 a day at the start, 25 after a week with at least 90% confirmed; your own lower limit still applies |
| Try it first | Already provided by the "Dry run" search state: forms are filled and screenshotted, nothing is submitted |

Still open in phase 3: practice forms for all eight systems (the acceptance test) and the 30-job review on real jobs in dry run; both need your live account.

## Version 2, phase 4 (first real submissions), prepared

| Change | Effect |
| --- | --- |
| Reviewed first | Admin → Users has a "Reviewed first" count. While a user sends for real, each of their first N applications (30 when real sending is switched on) waits for their approval; the page shows approved so far / N |
| Runbook | `docs/phase4-first-real-submissions.md`: the steps, the daily routine, the exit test and the stop rule |

The submissions themselves need the owner's account, a deployment of `v2-build`, and the owner's approvals; they
have not started.

## Version 2, phase 5 (new interface), first version on branch v2-build

| Screen | What it is now |
| --- | --- |
| Front door | Signed-out visitors see one sentence, three steps, "Create your account" and "Sign in" |
| Setup (`/setup`) | Four steps with resume: your CV (with "here is what I understood"), your Gmail and report address, what you want (in your own words, countries you may work in, visa yes/no), review with "Try it first" |
| Home (`/`) | Status line with Pause/Resume, "Needs you", this week, latest, and why jobs were skipped; fed by `GET /api/home` |
| Applications | Tabs All, Applied, Replied, Interview, Closed, Needs you, Skipped; search |
| Settings | Collapsed sections: what you want (state, daily number, how picky), CV and facts, where and how much, mail, reports, model keys, never contact, data and password |
| Menu | Home, Applications, Settings (Admin for operators) |

The old screens (Drafts, Inbox, Searches, Where we look, CVs, Account, setup chat at `/setup-chat`) still exist
as deep links that Settings points to; they are removed after the owner signs off each new screen. Not done in
phase 5: moving the CV, search-detail and block-list editors fully into Settings, rewriting `e2e/smoke.py` for the
new flow, the keyboard-only and 360-pixel passes, and the three-new-users test.

## Version 2, phase 6 (background and scale), on branch v2-build

| Change | Effect |
| --- | --- |
| One queued job per user | The five-minute tick no longer loops over every user itself; it queues one job per user (`jobs.tick_user`). With a worker they run in parallel; a user already running is skipped; each job has a time limit |
| A failing user | Recorded for the operator, never raised to the others (tested with 20 users, one failing) |
| Health | Admin page shows each user's last background pass: when, how long, and the first problem |
| Collection | Already its own background thread (harvest), not part of the tick |

Not yet proven: 20 simulated users through a full cycle within one hour on the real stack (needs a deployed worker).

## Version 2, phase 9 (CV tailoring client), on branch v2-build

| Change | Effect |
| --- | --- |
| `services/tailoring.py` | Client for `docs/CV_TAILORING_API.md`: bearer key, https only, idempotency key per person, job and purpose, waits 50 s then polls every 5 s for up to 5 minutes, downloads the PDF, deletes a person's data on account deletion |
| When it is used | Only if `CV_TAILOR_URL` and `CV_TAILOR_API_KEY` are set, the job passed the gates, scored at least 85, and an essential requirement is proven by a CV line but worded differently. Same kind of job and terms reuses the earlier tailored CV |
| Safety | The returned text is fact-checked against the original CV; anything new discards it. Any error, quota or timeout means the person's own CV. The tailored PDF is stored and used both for emails and for form uploads |

Off by default. Not yet run against the real service (it does not exist yet); the reply-rate comparison over 100 applications needs real use.

Not yet built from the roadmap: the labelled 200-posting regression set (needs the owner's "would apply" labels), per-provider spend column and key screens, the new interface (5), supply work (7), Gmail sign-in (8), 

284 backend tests pass (with the migration database from `scripts/init-db.sql`); lint passes.

Not yet done in phase 0: a physically separate live deployment (own Cloud Run service, database and Gmail
connection) for the owner's real job hunt, so the test site is only ever a test site. That is infrastructure, needs
the owner's approval to deploy, and is the first item to do together before any real submission work.

## Earlier history

### Since the first live runs (15 November 2026)

| Added | Notes |
| --- | --- |
| Searches built from the CV | A search without keywords uses the CV's own search terms; gaps are shown only as optional suggestions; keywords match whole terms |
| Country selection | Pick countries by name, strict filter, optional remote-from-elsewhere, Adzuna asked per country |
| Database migrations | Alembic baseline with row-level security; `python -m app.migrate` |
| No Redis needed | `REDIS_URL=postgres` keeps locks, rate limits and the page cache in Postgres |
| Private staging, deployed on every push to main | See `docs/staging.md`. Verified live: health, invite-only sign-up, noindex, CV upload and reading, search creation, discovery, agent run, scheduler tick |

161 backend tests pass, with Redis and with the Postgres stand-in.

Still to do on staging: connect Gmail there (the owner approves Google's screen), then send one practice
application to the sandbox inbox. Adzuna still needs a key; Arbeitnow alone has few remote .NET jobs.

## What exists and is tested

| Phase | Built | How it was tested |
| --- | --- | --- |
| 0. Foundations | Login, tenant isolation with row-level security, model adapter with cost recording and spend cap, worker framework, CI | Automated tests |
| 1. Apply by email | CVs and fact sheet, job sources, posting extraction, matching, writing with checks, send rules, at-most-once sending, places on a posting, agent loop with fallback, setup chat, scheduler, all screens | Automated tests, plus a real-browser run of the whole flow in demo mode |
| 2. Replies | Inbox sync limited to tracked threads, classification, answers from the fact sheet, escalations, human takeover, follow-ups, do-not-contact, Gmail push endpoint, digest, operator console | Automated tests |
| 3. Web forms | Form reading, answer planning, fill for review, submit after approval, stop conditions | Automated tests, including real Chrome against local pages |
| 4. Launch preparation | Notice emails, data export, account deletion, employer opt-out, health alerts, sign-in limit, container files, runbook | Automated tests. Billing was left out on the owner's instruction |

142 backend tests pass. The web app lints and builds. `e2e/smoke.py` passes.

## First live runs, 14 November 2026

Run on the owner's Windows PC with his Gemini key, Docker for Postgres and Redis, live job
sources and the practice mailbox (`scripts/live_run.py`). Nothing was sent to a real employer.

| Part | Result |
| --- | --- |
| Automated tests on Windows (Python 3.14) | 135 of 135 passed, including the real-Chrome form tests |
| Gemini: text, structured output, tool calling, embeddings | Work with the default model names |
| Arbeitnow, Greenhouse, Lever, Personio | All four return and parse live postings. Adzuna needs a key and was not tried |
| CV reading, posting reading, scoring | Work. A strong candidate now scores 92 to 98 on matching sample postings |
| Agent run | Finishes in about 21 steps and 3 to 4 minutes for 57 postings |
| Drafts | Three natural, correctly laid-out emails; all checks passed |
| Sending rules | Send window and the gap between emails held in the practice mailbox |
| Replies | Receipt confirmation logged, notice-period question answered from the fact sheet on the next working morning, interview invitation escalated as urgent with a draft answer |
| Website on Windows | Lints and builds. The browser test stopped at the CV upload; see the last row of the next table |

Bugs these runs found, all fixed and covered by tests:

| Found | Fix |
| --- | --- |
| The setup chat invented settings the user never gave (match threshold 70, follow-up after 5 days) | A setting is kept only if the model quotes the user's words for it and those words are in the conversation |
| The setup chat's confirmation named settings that were not saved, and said the CV and mailbox were missing when they were not | The confirmation is built from the stored settings; the "still needed" line is written by code |
| The agent spent all 40 steps on postings the user's own filters exclude | Code-only filters run over the whole pool first; they cost no model call and no step |
| A candidate meeting every must-have scored 80 because the employer's industry was unfamiliar | Each scoring dimension is defined; industry counts only when the posting requires it |
| Drafts and replies came back as one block of text | Layout is required in the prompt and checked; a block of text is sent back for a rewrite |
| "What is your notice period and when could you start?" was sorted as a salary question | Every reply category is defined for the model |
| The fact check could reject true statements (name, location, dates, total experience) | The profile text given to the check now carries them |
| Several tool answers in one agent turn were sent to Gemini as separate messages | They are grouped into one message |
| No retry on rate limits or overload; a cut-off JSON answer crashed the step | Transient errors are retried; invalid JSON is asked for once more |
| After saving, a page could reload its list before the save was committed (seen on the slower PC database: the CV list stayed empty) | Every request commits before its response is sent. Verified by a test, not yet re-run in the browser on Windows |
| Time zones failed on Windows, which ships no zone data | `tzdata` is a dependency |

What the live runs showed that is not a bug:

- **Live postings rarely print an email address.** All 54 Arbeitnow postings that matched "python" were web-form only. The email route was exercised with three sample postings. Phase 3 (web forms) will carry most real applications; plan the first real test around it, or around employers known to take email.
- **Arbeitnow's keyword match is loose** (it searches the full text), so many unrelated roles are read and then skipped. That costs a few cents per run, not correctness.
- The model's setup-chat reading still varies between runs (one run missed "Germany"). The confirmation now shows exactly what was saved, so the user sees and can correct it.

## What has never run against the real service

- **Gmail.** Sending, reading, token refresh and push were tested against responses shaped like Gmail's.
- **Adzuna.** Needs a free key.
- **Real application forms.** The form reader was tested on local pages only. Expect real hiring-system pages to need adjustments.
- **The owner's own CV and real postings he would apply to.** Writing quality was judged on one sample CV.

## What the phases' own "done when" tests still require

- Phase 1: 30 real applications sent by the owner in review mode, and the legal and security set in `docs/before-beta.md`.
- Phase 2: classification accuracy measured on 150 real or realistic messages, and two weeks of live use.
- Phase 3: 70% of forms on the supported hiring systems completing without help.
- Phase 4: Google verification, a penetration test, and a lawyer's review.

## Known gaps

- Embeddings are stored as arrays and compared in Python, not with pgvector.
- The company directory for per-company feeds starts empty; nothing fills it automatically yet.
- Notice emails need an SMTP service to be configured; without one, notices show only in the app.
- The evaluation sets from section 15 of the master document (extraction, scoring, fact check, classification on real data) are not built.
- Uploads are not virus-scanned; there is no two-factor sign-in.
