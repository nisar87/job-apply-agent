# Master product specification, version 2

Status: proposed, awaiting the owner's approval. Decisions and assumptions referred to as D1 to D9 and
A1 to A10 are in `PRODUCT_DECISIONS_V2.md`. Evidence for the changes is in `CURRENT_STATE_AUDIT.md`.

## 1. What the product is

A person uploads a CV, connects a Gmail account, says what work they want and presses Start. From then on
the agent finds suitable jobs, applies to them on the employers' own sites or by email, reads the replies,
answers the routine ones, and tells the person only when a decision is theirs to make.

The measure of the product is confirmed applications to jobs the person could realistically get, per unit
of the person's effort. It is not the number of features or of emails sent.

## 2. The lifecycle

Every job moves through these stages. Each stage has one owner (code or model), a success test and a
defined failure path. A job never moves forward on a guess.

| Stage | What happens | Decided by | Succeeds when | On failure |
| --- | --- | --- | --- | --- |
| 1. Collect | Jobs are gathered into the shared pool | Code | The posting is stored once, with its apply link | Source marked failing; others continue |
| 2. Read | The posting becomes structured fields: title, level, role family, required languages, country, work mode, sponsorship, apply route | Fast model | All gate fields are filled or marked unknown | Retried once, then set aside |
| 3. Gate | Hard rules remove jobs the user cannot take | Code | Passes every gate in section 4 | Stored as skipped, with the reason |
| 4. Score | Fit is scored against the CV | Strong model | Score with evidence for each must-have | Skipped as "could not score" |
| 5. Prepare | CV chosen (or tailored), answers and cover text written | Strong model, checked by code | Passes the fact check and the hiring-manager check | One rewrite, then skipped with the reason |
| 6. Submit | Form filled and submitted, or email sent | Code driving a browser, or mail | Confirmation as defined in section 6 | Section 6 |
| 7. Track | Replies are matched to the application and sorted | Fast model, code | Status updated | Shown under "Needs you" if unclear |
| 8. Respond | Routine questions answered; the rest escalated | Strong model within permissions | Reply sent or escalation raised | Escalated |
| 9. Follow up | One follow-up after silence, where allowed | Code | Sent once | Stops on any reply or request to stop |

## 3. Setup (the only work the user does)

Four steps, in this order, each on its own screen. Target: under five minutes, of which the user types for
under two.

1. **CV.** Upload. The agent reads it and shows what it understood: name, location, years of experience,
   role family and level, main skills, languages. The user corrects anything wrong in place.
2. **Gmail.** Connect the address applications go out from. A second address for reports is optional and
   defaults to the sign-in email.
3. **What you want.** One text box, pre-filled with a proposal written from the CV, for example: "Senior
   backend engineer roles using C# and .NET, remote or in countries you may work in, English-speaking."
   The user edits or accepts. Two questions always follow, because a CV cannot answer them (A5):
   which countries the user may work in today, and whether to apply where a visa must be sponsored.
   The user may also leave out jobs located in their own country (D10): the agent then applies only to
   remote jobs that hire from that country and to jobs abroad that offer sponsorship.
4. **Review and start.** One page: what the agent will do, what it will ask about, the daily limit, and a
   "Try it first" switch (section 9). Then **Start my agent**.

Everything else (limits, send hours, cooldowns, thresholds, sources, keys) has a working default and lives
under Settings. A user who never opens Settings gets a correctly working agent.

## 4. Gates (code, before any scoring)

These replace the current behaviour where the scorer alone decides. A job that fails any gate is never
scored, never drafted and never sent.

| Gate | Rule | Source of the user's side |
| --- | --- | --- |
| Country | The job's country is one the user chose, or the job is remote and hires from the user's country | Setup step 3 |
| Sponsorship | If the user has no right to work there, the ad must offer visa sponsorship or relocation | Setup step 3 |
| Language | Every language the job requires is one the user can work in | CV, confirmed in step 1 |
| Role family | The job belongs to a role family the agent derived from the CV (for example backend engineering), or one the user added | CV |
| Level | Within one level of the user's: no internships, trainee or junior roles for a senior person; no head-of or director roles unless the CV shows that level | CV |
| Freshness | Posted within the chosen period (default 14 days) and still open when checked | Default |
| Legitimacy | Not a known scam pattern, not an agency reposting without a named employer (when the user excludes agencies), not a duplicate of a job already handled | Code |
| History | Not applied to before; company and contact cooldowns respected; not on the user's never-contact list | Existing rules |

Unknown is treated per gate: an unknown required language passes; an unknown country fails unless the job
is clearly remote; an unknown level passes to scoring, where the scorer must state the level it inferred.

## 5. Scoring and the decision

- The scorer returns, per must-have requirement, whether the CV meets it and the CV line that proves it.
- A job is applied to when the score reaches the threshold (default 75) **and** no must-have marked
  essential is unmet.
- The agent orders the day's applications by score and by how recent the posting is, and stops at the
  daily limit.
- Scores and reasons are stored and shown in plain words ("Meets 6 of 6 must-haves; 15 years where 5 are
  asked").

## 6. Submission

### Routes, in order of preference

1. **Employer's form on a supported hiring system.** Supported at the start: Greenhouse, Lever, Ashby,
   Workable, SmartRecruiters, Recruitee, Personio, Teamtailor. Others are added by measured demand.
2. **Email**, only when the posting itself gives an application address.
3. **Anything else** (unsupported site, forced login, CAPTCHA): the job goes to "Needs you" with a direct
   link, and the agent learns which systems to support next from that list. The agent does not email an
   address it looked up, and does not create accounts or solve CAPTCHAs.

### Answering form questions

| Question type | Answer from |
| --- | --- |
| Name, contact, links, location | Profile |
| Work authorisation, sponsorship, notice period, start date, salary expectation, relocation | Fact sheet, asked once at first need and remembered |
| Skills and experience ("years of C#") | CV, with the same fact check as emails |
| Free text ("why this company") | Written by the strong model from the CV and the posting, fact-checked |
| Diversity and equal-opportunity questions | "Prefer not to say" unless the user set an answer |
| Legal declarations with no stored fact | Stop; "Needs you" |

A fact asked for the first time creates one question for the user; the answer is stored and reused for
every later form. The number of such questions should fall towards zero within the first week.

### What counts as submitted

An application is recorded as **submitted** only when one of these is true:

- the site shows its confirmation page or message after the final click, and the page address or content
  has changed from the form; or
- a confirmation email for that employer and role arrives in the connected mailbox within 30 minutes.

If neither happens the application is recorded as **unconfirmed**, a screenshot is stored, it is not
retried automatically (to avoid a double submission), and it appears under "Needs you" with the
screenshot. For email, "sent" means the mail provider accepted the message.

### Limits

- Daily limit per user: 10 at the start, raised automatically to 25 after a week with at least 90% of
  submissions confirmed (A7). The user may lower it.
- Never two applications to the same posting; company and contact cooldowns as today.
- At-most-once: the "submitting" state is saved before the final click or send.

## 7. Mail

- Two addresses: the **application Gmail** and the **report address**.
- Connection by Google sign-in with the narrowest permissions that allow sending and reading
  application threads. Until Google approves the app, invited users may use an app password (A9).
- Only threads that belong to an application, and confirmation emails from employers applied to, are read.
- Replies are sorted into: confirmation, rejection, question the agent may answer, interview, assessment,
  offer, redirect to a portal, request to stop, out of office, bounce, unclear.

## 8. Permissions

| The agent does without asking | The agent asks first | The agent never does |
| --- | --- | --- |
| Find, read, gate and score jobs | Any legal declaration without a stored fact | State experience, qualifications or facts not in the CV or fact sheet |
| Choose or request a tailored CV | Interview times | Bypass a CAPTCHA, login wall or site restriction |
| Submit forms and send emails within the limits | Salary figures not already stored | Email an address the posting did not give |
| Answer routine questions from stored facts | Accepting, declining or negotiating an offer | Contact anyone who asked not to be contacted |
| Follow up once | Anything the reply sorter marks unclear | Read mail unrelated to applications |
| Withdraw an application the user cancels | Assessments and tests | Share the user's data with anyone but the employer applied to |

Controls: **Pause** (finishes nothing new, keeps reading replies), **Resume**, **Stop everything** (halts
sending and submitting at once; nothing is retried until resumed), a full activity history, and the
"Needs you" list.

## 9. "Try it first" (simulation)

A switch at setup and in Settings. While on, the agent does everything except the final click and the
final send: forms are filled and screenshotted, emails are written and held. The user sees exactly what
would have gone out. It is off by default after the first start. Separately, the test site keeps practice
employers for end-to-end tests that contact nobody.

## 10. Models (D7)

| Work | Provider and tier | Why |
| --- | --- | --- |
| Reading postings, sorting replies, extracting form fields | Gemini, fast | High volume, simple |
| Scoring, writing, fact check, hiring-manager check, form free text, answering employers | Claude | Quality decides outcomes here |
| Web search for jobs | Gemini with search | Already built |

- Each user may add a Claude key and a Gemini key; the operator's keys are used where allowed.
- **Fallback:** when Claude answers with an authentication, billing, quota or repeated overload error, the
  step runs on Gemini's strong tier, the switch is logged, and the user sees one notice ("Claude key is
  not working; using Gemini"). Claude is tried again every hour. The reverse fallback also applies.
- Spending is recorded per provider per user with a daily limit; reaching it pauses new preparation, not
  reply handling.

## 11. CV tailoring service (AI CV Studio, D8)

Switched off until the service exists. The agent calls it only when a job passes the gates, scores at
least 85, and the CV's wording misses terms the posting treats as essential. One tailored CV is reused for
jobs in the same role family whose essential terms match.

The full build specification for the service is `CV_TAILORING_API.md`. In outline:

**Request** (HTTPS, JSON, API key in a header, an idempotency key per request):

| Field | Meaning |
| --- | --- |
| `base_cv` | The user's CV as a file reference, or the structured profile |
| `job` | Title, company, full posting text, language, country |
| `emphasis` | Skills and terms to bring forward, in order |
| `evidence` | For each term, the CV line that supports it (so nothing new is invented) |
| `omit` | Sections or items to shorten |
| `max_pages` | Usually 2 |
| `purpose` | `upload` for form uploads read by software (the service picks its best plain, single-column design), `email` for a designed one |
| `template` | Optional: a design named directly, overriding the choice made from `purpose` |
| `user_ref` | An opaque identifier for the user, never an email address, so the service can delete that user's results on request |
| `language` | Output language |

**Response:** the PDF, the same content as text, a list of every change made, and a `request_id`. Answer
within 60 seconds, or accept the request and call back.

**Agreed with the service's side (23 November 2026):** called only from the agent's server, never a browser, so
access is by API key and not by web origin or network address; address `tailor-api.aicvstudio.app`; the service
keeps its own model budget and answers `429` with a `quota_exhausted` code when it is used up, on which the agent
applies with the base CV; results are kept 7 days under the idempotency key (the same key with a different body is
refused with `409`), and the agent stores each PDF itself on receipt; the response names the design used.

**Rule the service must keep:** reorder, reword and shorten only. No fact that is not in `base_cv`. The
agent runs its own fact check on the returned text and discards a CV that fails.

## 12. Background operation

- One queued job per user per cycle, with its own lock and time limit; a slow user cannot delay others.
- Collection runs as its own scheduled job, not inside a web request.
- Every external action is idempotent or guarded by a saved state; every job can be retried safely.
- Health is visible to the operator: last successful run per user, queue length, failures by cause.

## 13. Data, security and privacy

Kept from version 1: per-user isolation in the database, encrypted tokens and keys, audit log, data export
and account deletion, employer opt-out, no request addresses in logs.

Added: screenshots of submissions stored per user and deleted with the account; signed-out visitors are
redirected to sign-in from every internal page; separate test and live environments, so that a code
change is exercised on practice employers before it can touch real ones.

Text from postings, forms and emails is untrusted. It is given to models only as data to extract from or
answer, never as instructions, and no model output can name a recipient or a link to submit to.

## 14. Acceptance criteria for version 2

| # | Criterion | Measured by |
| --- | --- | --- |
| 1 | A new user reaches "Start my agent" in under 5 minutes without help | Timed test with three people who have not seen it |
| 2 | At least 70% of gated, scored jobs on supported hiring systems are submitted and confirmed without the user | Counts over two weeks |
| 3 | No application to a job that fails a gate | Audit of every application in the two weeks |
| 4 | Of 50 applications reviewed by the owner, at least 45 are jobs he would have applied to himself | Owner's review |
| 5 | The user is asked at most 5 questions in the first week and at most 1 a week after | "Needs you" log |
| 6 | Every employer reply is matched to its application; sorting correct on at least 90% of 100 messages | Labelled sample |
| 7 | The agent runs for 14 days with no manual intervention by the operator | Operations log |
| 8 | Claude fallback: with the Claude key removed, a full cycle completes on Gemini | Test |

## 15. Job supply (D5)

The pool already holds over 100,000 jobs, yet one user saw 87 good matches in a week. Supply is therefore
judged by **applicable jobs per user per week**: jobs that pass every gate and can be submitted by the
agent. Raising it has four parts, in order of expected gain:

1. **Make form-only jobs submittable** (section 6). This alone turns most current "apply on the site"
   matches into applications.
2. **Widen what a search covers without lowering the bar:** role families and neighbouring titles derived
   from the CV; every country the user may work in; worldwide remote jobs that hire from the user's
   country; jobs offering sponsorship.
3. **Measure where jobs drop out.** A funnel per user (collected, read, passed each gate, scored, above
   threshold, submittable, submitted, confirmed) shown to the operator, so the largest loss is worked on
   first. Today this is not measured.
4. **More direct employer feeds** on the supported hiring systems, kept fresh (checked at least daily,
   closed jobs removed), and further hiring systems added by the count of good matches lost to them.

Sources that forbid automated use in their terms are not added.
