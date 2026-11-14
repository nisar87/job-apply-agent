# Before the first outside user

The master document requires this set at the end of phase 1. Status as of the first build:

| Item | Status |
| --- | --- |
| Tenant isolation in code and in the database | Done and tested: every endpoint is tried as another user |
| Mail tokens encrypted at rest, never returned by the API | Done and tested |
| At-most-once sending | Done and tested |
| Sandbox recipient outside production | Done and tested |
| Data export, account deletion, Gmail disconnect | Done and tested |
| Model-spend cap per user | Done and tested (default 1 US dollar a day) |
| Sign-in rate limit, security headers, upload checks | Done and tested |
| Kill switches (user, search, whole system) | Done and tested |
| Malware scan of uploads | Not done: add a scanner in front of the file store |
| Two-factor sign-in | Not done: arrives with the hosted identity provider |
| Backups and a restore test | Not done: configure on Cloud SQL |
| Privacy policy and terms of service | Not written: needs the company's name and a lawyer's short review |
| Data processing agreements with Google and the email service | Not done: the owner signs these |
| Data protection impact assessment | Not written |
| Google app published (unverified) with a privacy-policy link | Not done: needs the Google Cloud project |

Nothing in the "Not done" rows can be completed from code alone.
