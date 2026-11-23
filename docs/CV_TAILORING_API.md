# CV tailoring API

Build specification for the service AI CV Studio will provide to Job Apply Agent.
Version 1.0, 23 November 2026. Written by the calling side.

## 1. Purpose

Job Apply Agent sends one person's CV and one job. The service returns a version of that CV rearranged and
reworded for the job, as a PDF in a suitable design, together with a record of what it changed.

The service never adds a fact. That rule is the whole contract; section 6 defines it and section 12 tests it.

## 2. Who calls it

- One caller: Job Apply Agent's server (Google Cloud Run). No browser calls, so no CORS is needed.
- The caller has no fixed network address. Access is by API key only.
- Expected load at the start: under 50 requests a day, at most 2 at a time. Design for 20 at a time.

## 3. Address, transport, access

| Item | Value |
| --- | --- |
| Base address | `https://tailor-api.aicvstudio.app/v1` |
| Transport | HTTPS only; refuse plain HTTP |
| Format | JSON in UTF-8, except the PDF download |
| Access | Header `Authorization: Bearer <key>`. One key per caller, stored hashed, replaceable without downtime (accept two keys during a change) |
| Request size | Up to 8 MB |
| Rate limit | 60 requests a minute per key; answer `429` with `Retry-After` when exceeded |

Every response carries the header `X-Request-Id`. The caller logs it; quote it in any error investigation.

## 4. Endpoints

| Method and path | Purpose |
| --- | --- |
| `POST /v1/tailor` | Ask for a tailored CV |
| `GET /v1/tailor/{id}` | Read the status and result of a request |
| `GET /v1/tailor/{id}/pdf` | Download the PDF |
| `DELETE /v1/users/{user_ref}` | Delete everything stored for one person |
| `GET /v1/templates` | List the designs that can be asked for |
| `GET /v1/health` | Liveness check; no key needed |

## 5. `POST /v1/tailor`

### Headers

| Header | Required | Meaning |
| --- | --- | --- |
| `Authorization` | yes | The key |
| `Idempotency-Key` | yes | A string of up to 100 characters chosen by the caller. See section 8 |
| `Content-Type` | yes | `application/json` |

### Body

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `user_ref` | string, up to 64 | yes | An opaque identifier for the person. Never an email address or name |
| `base_cv.profile` | object | yes | The CV as structured data; section 5.1 |
| `base_cv.file` | object | no | The original file: `{ "name": "cv.pdf", "media_type": "application/pdf", "data": "<base64>" }`. PDF or DOCX, up to 5 MB. Use it for facts the profile lacks; where they disagree, the profile wins |
| `job.title` | string | yes | |
| `job.company` | string | yes | |
| `job.description` | string, up to 20,000 characters | yes | The full text of the posting |
| `job.language` | string | yes | Language of the posting, two letters (`en`, `de`) |
| `job.country` | string | no | Two letters |
| `emphasis` | array of objects | no | What to bring forward, most important first: `{ "term": "SQL Server", "evidence": "Perform large-scale SQL Server database audits" }`. `evidence` is a line from the CV that supports the term. A term whose evidence is not found in the CV is ignored and reported in `warnings` |
| `omit` | array of strings | no | Sections or items to shorten or leave out, for example `"Personal details"` |
| `max_pages` | integer, 1 to 3 | no | Default 2 |
| `purpose` | `upload` or `email` | yes | `upload`: the PDF will be read by hiring software. `email`: a person will read it |
| `template` | string | no | A design from `GET /v1/templates`, overriding the choice made from `purpose` |
| `language` | string | no | Output language, two letters. Default: the language of the CV. Translate only when it differs |
| `contact` | object | no | Contact details to print, replacing those in the CV: `{ "email": "...", "phone": "...", "location": "...", "links": ["..."] }` |

### 5.1 `base_cv.profile`

```json
{
  "name": "Maria Huber",
  "headline": "Senior Backend Engineer",
  "location": "Vienna, Austria",
  "summary": "Backend developer with six years of experience ...",
  "total_years_experience": 6,
  "skills": [ { "name": "Python", "years": 6 }, { "name": "PostgreSQL", "years": null } ],
  "roles": [
    {
      "title": "Senior Backend Developer",
      "company": "Alpenpay GmbH",
      "location": "Vienna",
      "start": "2022",
      "end": null,
      "achievements": [ "Built the payments API in Python and FastAPI, serving 4 million requests a day" ]
    }
  ],
  "education": [ "BSc Computer Science, TU Wien, 2019" ],
  "certifications": [],
  "languages": [ { "name": "German", "level": "native" } ]
}
```

`end: null` means the role is current. Any field may be missing or empty; print only what is given.

### Answers

The service works on the request for up to 50 seconds before answering.

- **`200`**: finished within that time. Body as in section 5.2 with `status: "completed"`.
- **`202`**: still working. Body `{ "id": "...", "status": "processing" }`. The caller polls
  `GET /v1/tailor/{id}` every 5 seconds for up to 5 minutes.
- **Errors**: section 9.

A request must finish, or fail, within 5 minutes.

### 5.2 Result

```json
{
  "id": "tlr_01HZX3...",
  "status": "completed",
  "created_at": "2026-11-23T05:12:44Z",
  "expires_at": "2026-11-30T05:12:44Z",
  "template": "meridian",
  "pages": 2,
  "language": "en",
  "pdf_path": "/v1/tailor/tlr_01HZX3.../pdf",
  "text": "MARIA HUBER\nSenior Backend Engineer\n...",
  "sections": [ { "heading": "Experience", "text": "..." } ],
  "changes": [
    { "kind": "reordered", "where": "Skills", "detail": "PostgreSQL and Celery moved to the front" },
    { "kind": "reworded",  "where": "Alpenpay GmbH, bullet 2",
      "before": "Moved the reporting jobs from cron scripts to Celery and PostgreSQL",
      "after":  "Rebuilt background reporting jobs on Celery and PostgreSQL" },
    { "kind": "shortened", "where": "Donau Logistics", "detail": "Two bullets merged" },
    { "kind": "removed",   "where": "Personal details", "detail": "Date of birth" }
  ],
  "warnings": [ "emphasis term 'Kubernetes' has no evidence in the CV and was ignored" ],
  "usage": { "input_tokens": 5120, "output_tokens": 1840, "cost_usd": 0.012 }
}
```

| Field | Rule |
| --- | --- |
| `text` | Exactly the words printed in the PDF, in reading order. The caller fact-checks this text, so it must not differ from the PDF |
| `changes` | Every change, with one of four kinds: `reordered`, `reworded`, `shortened`, `removed`. For `reworded`, give `before` and `after`. There is no kind for "added" because nothing may be added |
| `warnings` | Anything asked for and not done, with the reason |
| `status` | `processing`, `completed` or `failed`. A failed result carries `error` as in section 9 |

## 6. The rule: no new facts

Allowed: change the order of sections, roles' bullets and skills; reword a sentence without changing what
it claims; shorten or merge; leave out; translate when asked; choose the design.

Not allowed, with examples:

| Not allowed | Example |
| --- | --- |
| A skill, tool or technology not in the CV | The CV says ".NET Framework"; the output says "ASP.NET Core" |
| A stronger claim than the CV makes | "Mentored junior developers" becomes "Led engineering teams" |
| New or changed numbers, dates, employers, titles, degrees | "40%" becomes "over 40%"; "2012" becomes "2011" |
| A job title changed to match the posting | "Software Developer" becomes "Software Architect" |
| Wording copied from the posting presented as the person's experience | "Experienced in microservices" when the CV never mentions them |
| Keywords added in hidden or white text | Any text in the PDF that a reader cannot see |

How to build it so the rule holds: generate the tailored content as structured data in which every
sentence carries a reference to the CV line or lines it came from; drop any sentence without a reference;
then run a second check that compares each output sentence with its source and rejects a stronger claim.
If the check still fails after one retry, return `failed` with code `could_not_tailor_faithfully`. A
faithful CV with little change is a correct result; a better-matching CV with an invented fact is not.

## 7. Designs

- `GET /v1/templates` returns `[{ "id": "meridian", "name": "Meridian", "purposes": ["email"], "premium": true }]`
  for every live design. Draft designs are not listed and cannot be asked for.
- `purpose: "email"` chooses the best designed template for the content length.
- `purpose: "upload"` chooses a design that meets all of these, because hiring software must parse it:
  one column; all text selectable (no text as images); no text boxes, tables or headers and footers
  carrying content; standard fonts embedded; section headings in plain words ("Experience", "Education",
  "Skills"); no icons in place of words; dates in one consistent format; file under 1 MB.
- The caller may name a design in `template`. An unknown or draft name answers `422`.
- The PDF's document title is the person's name and headline. The file name offered on download is
  `<Name>-CV.pdf` with spaces replaced by hyphens.

## 8. Repeats and storage

- The same `Idempotency-Key` with the same body returns the stored result, with header
  `Idempotent-Replay: true`, and uses no model budget.
- The same key with a different body answers `409` with code `idempotency_conflict`.
- Results, including the PDF, are kept for 7 days and then deleted. After that the id answers `404`.
- Requests and results are stored encrypted at rest. They are used only to answer the caller: not for
  training, samples, analytics about the person, or any other product.
- Logs hold ids, sizes, timings and error codes. They never hold CV text, posting text or contact details.

## 9. Errors

Every error has this shape:

```json
{ "error": { "code": "quota_exhausted", "message": "Daily model budget reached", "retry_after_seconds": 3600 } }
```

| Status | `code` | When | What the caller does |
| --- | --- | --- | --- |
| 400 | `bad_request` | Body is not valid JSON | Fix; do not retry |
| 401 | `unauthorized` | Missing or wrong key | Alert the operator |
| 404 | `not_found` | Unknown or expired id | Send a new request |
| 409 | `idempotency_conflict` | Same key, different body | Fix; do not retry |
| 413 | `too_large` | Body over 8 MB or file over 5 MB | Send the profile without the file |
| 422 | `invalid_field` | A field fails section 5; `message` names it | Fix; do not retry |
| 422 | `could_not_read_cv` | The profile is empty and the file cannot be read | Apply with the base CV |
| 422 | `could_not_tailor_faithfully` | No result passed the check in section 6 | Apply with the base CV |
| 429 | `rate_limited` | Over the rate limit; `Retry-After` set | Retry after the delay |
| 429 | `quota_exhausted` | The service's own model budget is used up | Apply with the base CV; retry next day |
| 500 | `internal_error` | Unexpected failure | Retry up to 2 times with the same key |
| 503 | `unavailable` | Temporarily down; `Retry-After` set | Retry after the delay |

On any failure the caller applies with the person's base CV, so an error never blocks an application.

## 10. Budget

The service has its own model key or quota, separate from the rest of AI CV Studio, with a daily limit.
It reports `usage` on every result. When the limit is reached it answers `quota_exhausted`, as above.

## 11. Deleting a person's data

`DELETE /v1/users/{user_ref}` removes every stored request, result and PDF for that reference and answers
`200` with `{ "deleted": 12 }`. It answers `200` with `{ "deleted": 0 }` for an unknown reference. The
caller sends it when a person deletes their account.

## 12. Acceptance tests

The service is ready when all of these pass. The caller will run them with its own sample CVs.

| # | Test | Passes when |
| --- | --- | --- |
| 1 | Tailor a CV for a matching job, `purpose: upload` | `200` or `202` then `completed`; PDF downloads; `text` equals the PDF's text; at most `max_pages` pages |
| 2 | Open the `upload` PDF with a text extractor | Every section and line comes out in reading order with no missing or doubled text |
| 3 | Posting asks for a skill the CV lacks | The skill does not appear anywhere in `text` |
| 4 | Posting uses stronger words than the CV ("led", "architected") | No claim in `text` is stronger than its source line |
| 5 | Every number, date, employer and title in `text` | Each appears identically in the CV |
| 6 | `emphasis` term without evidence | Ignored and listed in `warnings` |
| 7 | Same key, same body, sent twice | Identical result; second has `Idempotent-Replay: true`; no extra usage |
| 8 | Same key, different body | `409 idempotency_conflict` |
| 9 | Wrong key | `401` |
| 10 | Draft or unknown `template` | `422 invalid_field` |
| 11 | Budget set to zero | `429 quota_exhausted` |
| 12 | `DELETE` for a `user_ref`, then `GET` an earlier id | `404` |
| 13 | A result 7 days old | `404` |
| 14 | 20 requests at once | All finish within 5 minutes |
| 15 | `omit: ["Personal details"]` | Date of birth and nationality absent from `text` |
| 16 | `contact.email` given | That address is printed; the CV's own address is not |

## 13. Example

```http
POST /v1/tailor
Authorization: Bearer <key>
Idempotency-Key: user-7f3a:job-91c2:v1
Content-Type: application/json

{
  "user_ref": "u_7f3a9c",
  "base_cv": { "profile": { "name": "Maria Huber", "headline": "Senior Backend Engineer", "roles": [ ... ] } },
  "job": {
    "title": "Senior Python Developer",
    "company": "Bergblick Software GmbH",
    "description": "We are looking for ... At least 5 years of Python ... PostgreSQL ... Celery ...",
    "language": "en",
    "country": "at"
  },
  "emphasis": [
    { "term": "PostgreSQL", "evidence": "Moved the reporting jobs from cron scripts to Celery and PostgreSQL" },
    { "term": "Celery",     "evidence": "Moved the reporting jobs from cron scripts to Celery and PostgreSQL" }
  ],
  "omit": ["Personal details"],
  "max_pages": 2,
  "purpose": "upload",
  "contact": { "email": "maria.jobs@example.com" }
}
```

## 14. What the caller will send, and when

For planning only. The agent asks for a tailored CV when a job has passed its filters, scores 85 or more,
and the CV's wording misses terms the posting treats as essential. It reuses one tailored CV for similar
jobs. It builds the idempotency key from the person, the job and a version number, so a repeat costs
nothing. It always fact-checks `text` itself and discards a CV that fails.
