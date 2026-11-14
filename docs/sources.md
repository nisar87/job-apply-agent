# Job source register

A source is enabled only when its row is complete. Re-check monthly.

| Source | Access | Terms to respect | Rate limit used | Last checked | Status |
| --- | --- | --- | --- | --- | --- |
| Adzuna API | Free key per developer | Attribution; ongoing commercial use needs a licence from Adzuna | One search per campaign and country every 6 hours | Not yet checked at the source | Enable for personal use; get the licence before a paid launch |
| Arbeitnow API | Public, no key | Check the API page for current terms | One request every 6 hours per campaign | Not yet checked at the source | Enabled |
| Greenhouse job boards | Public Job Board API per company | Published postings only | One request per company every 6 hours | Not yet checked at the source | Enabled when a company is in the directory |
| Lever postings | Public postings API per company | Published postings only | Same | Not yet checked at the source | Enabled when a company is in the directory |
| Personio XML feed | Public feed per company | Published postings only | Same | Not yet checked at the source | Enabled when a company is in the directory |
| Recruitee | Per-company token required from 10 February 2027 | — | — | — | Not implemented |
| Public job pages | Polite fetch of single pages | robots.txt obeyed; never logged in; blocks never bypassed | One request every 3 seconds per domain; 24-hour cache | — | Used only to complete a posting a source returned as a snippet, and for links a user pastes |
| LinkedIn, Indeed and similar | — | Their terms forbid automation | — | — | Never automated |

"Not yet checked at the source" means the terms were not readable from the build
environment. Read each provider's current terms before enabling it for other users.
