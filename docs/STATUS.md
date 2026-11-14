# Build status

Last updated after the first build session.

## What exists and is tested

| Phase | Built | How it was tested |
| --- | --- | --- |
| 0. Foundations | Login, tenant isolation with row-level security, model adapter with cost recording and spend cap, worker framework, CI | Automated tests |
| 1. Apply by email | CVs and fact sheet, job sources, posting extraction, matching, writing with checks, send rules, at-most-once sending, places on a posting, agent loop with fallback, setup chat, scheduler, all screens | Automated tests, plus a real-browser run of the whole flow in demo mode |
| 2. Replies | Inbox sync limited to tracked threads, classification, answers from the fact sheet, escalations, human takeover, follow-ups, do-not-contact, Gmail push endpoint, digest, operator console | Automated tests |
| 3. Web forms | Form reading, answer planning, fill for review, submit after approval, stop conditions | Automated tests, including real Chrome against local pages |
| 4. Launch preparation | Notice emails, data export, account deletion, employer opt-out, health alerts, sign-in limit, container files, runbook | Automated tests. Billing was left out on the owner's instruction |

135 backend tests pass. The web app lints and builds. `e2e/smoke.py` passes.

## What has never run against the real service

The build environment could not reach these, so the code is written to their documented
interfaces and tested against stand-ins:

- **Gemini.** Run `scripts/check_gemini.py` first. Model names in `.env` may need changing.
- **Gmail.** Sending, reading, token refresh and push were tested against responses shaped like Gmail's.
- **Job sources.** Adzuna, Arbeitnow, Greenhouse, Lever and Personio parsers were tested against sample responses, not live ones.
- **Real application forms.** The form reader was tested on local pages only. Expect real hiring-system pages to need adjustments.
- **The writing quality.** Every draft in the tests came from a scripted stand-in. Only real Gemini runs show how good the emails are.

## What the phases' own "done when" tests still require

- Phase 1: 30 real applications sent by the owner in review mode, and the legal and security set in `docs/before-beta.md`.
- Phase 2: classification accuracy measured on 150 real or realistic messages, and two weeks of live use.
- Phase 3: 70% of forms on the supported hiring systems completing without help.
- Phase 4: Google verification, a penetration test, and a lawyer's review.

## Known gaps

- No database migrations yet; `python -m app.schema` creates tables but does not alter them.
- Embeddings are stored as arrays and compared in Python, not with pgvector.
- The company directory for per-company feeds starts empty; nothing fills it automatically yet.
- Notice emails need an SMTP service to be configured; without one, notices show only in the app.
- The evaluation sets from section 15 of the master document (extraction, scoring, fact check, classification on real data) are not built.
- Uploads are not virus-scanned; there is no two-factor sign-in.
