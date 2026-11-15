# Job source register

A source is enabled only when its row is complete. Re-check monthly.

| Source | Access | Terms to respect | Rate limit used | Last checked | Status |
| --- | --- | --- | --- | --- | --- |
| Adzuna API | Free key per developer | Attribution; ongoing commercial use needs a licence from Adzuna | One search per campaign and country every 6 hours | Not yet checked at the source | Enable for personal use; get the licence before a paid launch |
| Arbeitnow API | Public, no key | Check the API page for current terms | One request every 6 hours per campaign | Not yet checked at the source | Enabled |
| Greenhouse job boards | Public Job Board API per company | Published postings only | One request per company every 6 hours | Not yet checked at the source | Enabled when a company is in the directory |
| Lever postings | Public postings API per company | Published postings only | Same | Not yet checked at the source | Enabled when a company is in the directory |
| Personio XML feed | Public feed per company | Published postings only | Same | Not yet checked at the source | Enabled when a company is in the directory |
| Remotive API | Public, no key | Link back to the job on Remotive and name Remotive as the source; no more than about 4 requests a day; jobs may not be resubmitted to other boards | Software category fetched once every 6 hours for all users (shared cache) | 15 Nov 2026 (legal notice in the API response) | Enabled |
| Jobicy API v2 | Public, no key | Credit Jobicy with a link; applications go to the job's own URL | Latest 100 engineering jobs, once every 6 hours (shared cache) | 15 Nov 2026 (notice in the API response) | Enabled |
| Himalayas API | Public, no key (a free key raises limits) | Use the application link unchanged; name Himalayas as the source; never pass jobs on to other boards, aggregators or Google Jobs; remove expired jobs | Search per keyword (up to 4), each cached 12 hours; data refreshes daily | 15 Nov 2026 (himalayas.app/api) | Enabled |
| We Work Remotely RSS | Public RSS (back-end and full-stack categories) | Link back to each post (terms page not readable; robots.txt allows) | Each feed once every 6 hours (shared cache) | 15 Nov 2026 | Enabled |
| Remote OK API | Public, browser-like User-Agent | "Please link back to the URL on Remote OK and mention Remote OK as a source", or access is suspended | Once every 6 hours (shared cache) | 15 Nov 2026 | Enabled |
| Working Nomads API | Public `/api/exposed_jobs/` | Terms page missing; robots.txt allows everything | Once every 6 hours | 15 Nov 2026 | Enabled (small) |
| Ashby job boards | Public posting API per company, no key | Docs: data may be used to populate job listings | Each company board at most twice a day | 15 Nov 2026 | Enabled when a company is in the directory |
| Workable job boards | Public widget endpoint per company | No published terms for the widget; used by Workable's own embed | Same | 15 Nov 2026 | Enabled when a company is in the directory |
| SmartRecruiters | Keyless posting API per company (docs now also offer keys; the keyless path may be removed) | — | Same | 15 Nov 2026 | Enabled when a company is in the directory |
| Recruitee | Public careers API `{slug}.recruitee.com/api/offers/` (no key; OpenAPI shows no security). The token rule applies to the separate ATS API | — | Same | 15 Nov 2026 | Enabled when a company is in the directory |
| Teamtailor | Public `jobs.rss` per company (the API needs a company key) | — | Same | 15 Nov 2026 | Enabled when a company is in the directory |
| Hacker News "Who is hiring" | Algolia HN API, monthly thread | Thread asks to email only when genuinely interested | — | 15 Nov 2026 | Planned (needs the model to read each free-text post) |
| Jooble, Reed.co.uk, Careerjet, Findwork | Free keys on registration | Built for publishers/portals | — | 15 Nov 2026 | Ready to add once the owner registers keys |
| The Muse, EURES, Rozee.pk, Bayt, GulfTalent, Remote.co, JustRemote, Wellfound, Dice | No usable public API, blocked, or terms unclear | — | — | 15 Nov 2026 | Not used |
| Public job pages | Polite fetch of single pages | robots.txt obeyed; never logged in; blocks never bypassed | One request every 3 seconds per domain; 24-hour cache | — | Used only to complete a posting a source returned as a snippet, and for links a user pastes |
| LinkedIn, Indeed and similar | — | Their terms forbid automation | — | — | Never automated |

Company boards are kept in the `job_sites` table: they are learned automatically from links in job ads,
checked in turn (oldest first, at most twice a day each), and paused after five failed checks in a row or as
soon as the board no longer exists. The "Where we look" page lists every source with its status.

"Not yet checked at the source" means the terms were not readable from the build
environment. Read each provider's current terms before enabling it for other users.
