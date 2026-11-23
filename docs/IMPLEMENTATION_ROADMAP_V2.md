# Implementation roadmap, version 2

Status: proposed. Nothing here is built until the owner approves the specification.

## Rules for the work

- Built on a branch and reviewed as a pull request. Nothing reaches the live site without the owner's
  approval of that deployment.
- Before phase 1, the live account's automatic sending is switched to "Try it first" so that no further
  applications go out on the current matching. This needs the owner's agreement.
- Each phase ends with its own tests passing and its completion criteria measured, before the next begins.
- Durations assume one builder working with the owner's reviews and are estimates, not commitments.

## Phases

| Phase | Purpose | Main work | Depends on | Done when | Estimate |
| --- | --- | --- | --- | --- | --- |
| 0. Safety and truth | Stop harm; measure | Separate test and live environments; redirect signed-out visitors; fix the date-dependent test; funnel counts per user; remove looked-up addresses; update `STATUS.md` | none | Funnel visible to the operator; no email leaves to an address not in the ad | 3 to 4 days |
| 1. Matching that can be trusted | No wrong applications | Gate fields in posting reading (level, role family, required languages, sponsorship); gates in code; role family and level derived from the CV; duplicate fix; evidence per must-have | 0 | On a labelled set of 200 real postings, no gate failure passes and at least 90% of the owner's "would apply" jobs pass | 1 to 1.5 weeks |
| 2. Models | Better judgement, no single point of failure | Claude provider behind the existing adapter; routing by task; fallback both ways; per-provider spend; key screens | 0 | Acceptance criterion 8; scoring and writing run on Claude in tests and one live cycle | 3 to 4 days |
| 3. Form submission | The main route works | Per-system handlers for the eight hiring systems; fact questions asked once; confirmation by page or email; screenshots; unconfirmed handling; daily limit with automatic rise; "Try it first" | 1 | On practice forms for all eight systems, 100% correct; on real jobs in "Try it first", at least 70% reach the final step with correct answers on the owner's review of 30 | 3 to 4 weeks |
| 4. First real submissions | Proof | Owner's account, limit 10 a day, every submission reviewed for the first 30 | 3, owner's approval | 30 confirmed real submissions, none to a job the owner rejects | 1 to 2 weeks elapsed |
| 5. New interface | Understandable without study | Front door; four-step setup; Home; Applications; Settings; report email; old screens removed | 1 (for the CV summary) | Acceptance criterion 1 with three new users; owner signs off each screen | 2 to 3 weeks, can overlap phase 3 |
| 6. Background and scale | More than a few users | One queued job per user; collection as its own job; operator health view | 0 | 20 simulated users complete a cycle within one hour; a failing user does not delay others | 1 week |
| 7. Supply | More applicable jobs | Work on the largest funnel loss; more employer feeds; next hiring systems by lost matches | 0 funnel, 3 | Applicable jobs per week for the owner at least triple the baseline measured in phase 0 | ongoing, first pass 1 week |
| 8. Gmail sign-in | Setup as designed | Google verification and security assessment; narrow permissions | owner's budget (Q2) | Approved by Google; new users connect with one click | weeks, mostly waiting |
| 9. CV tailoring | Better fit per job | Client for the interface in specification section 11; decision rule; fact check of returned CVs | AI CV Studio's side (Q3) | Tailored CVs pass the fact check; reply rate compared with untailored over 100 applications | 1 week after the service exists |

Phases 0 to 5 together: roughly 8 to 11 weeks.

## Data changes

| Change | Phase | Migration |
| --- | --- | --- |
| Posting fields: level, role family, required languages, sponsorship offered | 1 | New columns; existing postings re-read in the background |
| User profile: languages, role families, level, countries with right to work, sponsorship choice | 1 | New columns filled from the CV and fact sheet; owner confirms once |
| Application: route, confirmation type, confirmation evidence, screenshot reference | 3 | New columns; existing rows marked "email, sent" |
| Fact questions asked and answered | 3 | New table |
| Model usage per provider | 2 | Column added to the usage table |
| Search settings folded into the user's preferences | 5 | Existing searches converted; the old table kept until phase 5 is signed off |

## Tests added

| Area | Tests |
| --- | --- |
| Gates | One test per gate and per "unknown" rule; the labelled set of 200 postings as a regression suite |
| Forms | A local copy of each hiring system's form layouts; submission confirmed and unconfirmed paths; no double submission after a crash at any step |
| Models | Routing per task; each fallback trigger; spend limits per provider |
| Interface | Browser test of setup to first application in "Try it first"; keyboard-only pass; 360-pixel pass |
| Background | A stuck user job does not block others; every job safe to repeat |

## What is measured from phase 0 onward

Setup time; questions asked per user per week; applicable jobs per week; share of submissions confirmed;
share of applications the owner would have made himself; reply sorting accuracy; replies and interviews
per 100 applications; cost per application; days without operator intervention.

## Risks

| Risk | Effect | Response |
| --- | --- | --- |
| Hiring systems add bot checks | Fewer forms completed | Stop and list under "Needs you"; never bypass; track by system |
| A site's form changes | Wrong or failed submission | Confirmation rule prevents false "submitted"; layout tests; alerts on a falling confirmation rate |
| Applications go out with a wrong answer | Harm to the user's name | Facts only from stored answers; first 30 reviewed; "Try it first" |
| Google's review is slow or refused | Setup stays harder for new users | App password remains for invited users |
| Few jobs pass the gates for some users | Low volume | Funnel shows why; the agent tells the user which choice limits it most |
