# Build status

Last updated 15 November 2026, after the private staging deployment.

## Since the first live runs (15 November 2026)

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
