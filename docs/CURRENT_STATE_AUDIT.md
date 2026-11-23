# Current state audit

Audit date: 23 November 2026. Code at commit `07df4ed` (18 November). Live site: https://jobs.aicvstudio.app.

## How this was checked

| Source | What was done |
| --- | --- |
| Repository | Read in full, including the 42 commits of 15 to 18 November. Test suite run locally |
| Live site, signed out | Home, sign-in, sign-up, forgot password, employers page, robots file, health endpoint |
| Live site, signed in (owner's account, read-only) | Today, Applications, Drafts, Inbox, Searches, Where we look, CVs and facts, Account. Three sent applications opened |
| Not checked | Setup chat with data, Admin, the filled-form review screen, mobile layout of signed-in pages, the database itself, server logs. Nothing was clicked that sends, submits or changes data |

## Verdict

The product runs every day without the user and writes natural emails, but it does not yet do its central
job. In the owner's account over seven days it checked 390 postings, found 87 good matches, sent 22 emails,
submitted no web form and produced no interview. 56 good matches wait under "apply on the employer's site",
which hands the work back to the user.

## Capability status

Status words: **verified** (seen working on real services), **untested** (built, automated tests only),
**partial**, **broken**, **missing**, **unverifiable** (could not be checked in this audit).

| Capability | Status | Evidence |
| --- | --- | --- |
| Reading a CV into a profile | Verified | Live runs of 14 November; profile used in live drafts |
| Fact sheet filled from the CV | Partial | A draft stating the user's city and readiness to relocate was rejected as an "unsupported claim" (Drafts, CIS (Pvt) Ltd). Whether the fact sheet holds those facts could not be read |
| Understanding plain-language instructions | Partial | Setup chat exists and guards against invented settings (`agent/onboarding.py`), but the Searches screen still exposes about 28 controls the user must understand |
| Job discovery | Verified, with a question | Pool of 116,287 jobs, 1,530 new in a day. Most boards show their last good check on 16 November; only the pool, Adzuna and Google search show 22 November. Not yet known whether that is by design |
| Duplicate detection | Partial | Logiciel Services and MacroAgility each appear twice in Applications and Drafts |
| Matching | Broken in important cases | Score 100 for a paid internship and 88 for an entry-level IT support technician, for a 15-year engineer. "Head of Engineering (Frontier Safety)" scored 95 and "Lead Solution Expert" 75; both were sent |
| Language requirement | Missing as a gate | Applied to adesso, whose reply says most roles need C1 German. `matching.py` filters on the posting's language, not on the language the job requires |
| Work eligibility by country | Partial | Country filter exists; remote jobs that cannot hire from the user's country are skipped. No notion of visa or sponsorship |
| Writing application emails | Verified | Natural, correctly laid out. Name spelled two ways across drafts ("Ahmad", "Ahmed") |
| Checks before sending | Verified, sometimes wrong | Correctly blocked weak drafts; also blocked true statements for lack of facts |
| Sending by email | Verified | 22 sent from the user's job Gmail. One went to `info@adesso.de`, a general address the agent looked up (`services/contacts.py`) |
| Web-form submission | Partial, never proven | Reader, answer planning and fill exist (`services/forms.py`). Two forms filled since 17 November, none submitted. Success is judged by "thank you" phrases (`forms.py` lines 37 to 44, 440 to 442). On the live site real employers' forms are refused unless the admin switches the user to real sending (`_check_form_sandbox`) |
| Gmail connection | Verified, wrong method | Google app password over IMAP and SMTP (`GmailAppPasswordProvider`). The older OAuth path is no longer offered |
| Reading and sorting replies | Verified on one real reply | adesso's reply was matched to its application and sorted as "apply through their portal", with a suggested answer |
| Bounce handling | Broken detail | Notice reads "Your application to (the user's own address) could not be delivered" |
| Automatic replies | Untested on real mail | Practice mailbox only |
| Follow-ups | Unverifiable | None seen in the account |
| Background operation | Verified for one user | Cloud Scheduler calls the API, which processes every user in turn inside one request (`tasks/scheduler.py` `tick`); the harvester runs in a thread on a service that scales to zero |
| Sign-in, per-user isolation | Untested beyond tests | Row-level security with tests. Signed-out visitors can open the empty shells of `/account`, `/operator`, `/campaigns` and `/setup`; no data is shown |
| Simulation without contacting employers | Partial | Practice job ads and forms exist on the site; no single switch a user can see |
| Tests and CI | Mostly healthy | 250 tests: 247 pass locally, 2 fail for lack of a local database, 1 (`test_contacts`) passes in CI on 18 November and fails on 23 November on the same code; cause not yet diagnosed |
| Documentation | Out of date | `docs/STATUS.md` was last updated 15 November and describes sending as sandboxed |

## Live site review

| Question | Finding |
| --- | --- |
| Does the site say what it is? | No. The address opens "Sign in". One sentence on the sign-up page is the only description |
| Can a non-technical person start? | No. They must generate a Google app password, get a Gemini key (or be allowed the operator's), fill a fact sheet and understand a settings form |
| Steps before the agent can work | Account, CV, job Gmail with app password, main email, fact sheet, a search. Six, several with sub-steps |
| Too many settings? | Yes. Nine menu entries plus Admin; about 28 controls on Searches (threshold, cooldowns, send window, draft expiry and more) |
| Can the user tell what the agent is doing? | Partly. "Today" shows counts, but "0 replies" sits beside a chart with reply bars and "1 reply waiting" |
| Are statuses visible? | Yes, but 11 statuses and three tabs, with "Apply by hand" dominating |
| Inbox | One real reply sits among about 20 system notices |
| Where we look | A list of 17 boards and 300 of 20,874 company pages under internal names such as `spacexgreenhouse` |
| Assistant or software? | Software. The user operates it; it does not report to the user |

## Where it is going wrong

1. **The channel drives the product.** Real postings rarely give an email address. Instead of making form
   submission dependable, effort went into finding email-able jobs, including looking up companies' general
   addresses. Those emails reach the wrong inbox; adesso's answer says so directly.
2. **The essential capability is the least proven.** No real form has been submitted.
3. **Matching cannot yet be trusted with automatic sending.** New searches default to sending everything
   that passes the checks (`api/core.py` line 168), while the scorer rates internships and unrelated
   leadership roles highly.
4. **Setup contradicts the vision.** An app password is harder to set up than Google sign-in and grants the
   whole mailbox.
5. **Dashboard, not assistant.** Internal concepts (drafts, sources, thresholds, cooldowns, AI keys) are the
   interface.
6. **Volume is limited by fit and eligibility, not by collection.** The pool is large; few jobs survive
   country, language and role filters, and those that do are form-only.
7. **Process.** 42 commits in four days deployed straight to the live site, which sends real applications.

## Keep, improve, replace, remove

| Part | Decision | Why |
| --- | --- | --- |
| Rules enforced in code (quotas, cooldowns, no repeats, at-most-once) | Keep | Proven in tests and live |
| Row-level security, encrypted secrets, audit log | Keep | Sound foundation |
| CV reading, email writing, pre-send checks | Keep, improve facts | Quality is good; needs complete facts |
| Reply sorting and escalation | Keep | Worked on the real reply |
| Job pool and harvester | Keep, hide | Useful supply; not a user screen |
| Matching | Improve | Add hard gates before scoring |
| Web forms | Improve, make primary | Only route to most jobs |
| Looking up general company addresses | Remove | Wrong inbox, legal and reputation risk |
| Gmail app password | Replace for the product | Google sign-in; app password stays for the owner's testing until Google approves the app |
| Searches screen, Where we look, Drafts as a work queue | Replace | New interface |
| Scheduler inside one web request | Replace before more users | One queue job per user |
| Model access | Improve | Claude for the hard steps, Gemini otherwise, automatic fallback |

## Readiness

Scores are 0 to 10 and rest only on what was observed.

| Area | Score | Basis |
| --- | --- | --- |
| Interface and ease of use | 3 | No front door; six setup steps; owner reports not understanding it |
| Functional completeness | 5 | Email path complete; form path incomplete |
| Real automation | 3 | 22 of 87 good matches applied to automatically, all by email; 0 forms |
| Form submission reliability | 1 | Never run on a real form |
| Gmail communication reliability | 5 | Sends and reads; one real reply handled; bounce mislabelled; automatic answers unproven |
| Security and privacy | 5 | Good isolation and encryption; full-mailbox credential; unsolicited mail to general addresses |
| Operations | 4 | Runs daily for one user; design does not scale; no staging separate from real sending |
| Overall | 3 | Does not yet achieve the central objective |
