# Private staging

A hidden copy of the app for testing with real services before any public launch.

| | |
| --- | --- |
| Address | https://jaa-x7k2.nisarworks.com (also https://job-apply-agent-staging.web.app) |
| Firebase / Google Cloud project | `job-apply-agent-staging`, its own project; billing account "Firebase Payment", budget alert US$20/month |
| Owner account | noreply.dotnetobject@gmail.com |
| DNS | one CNAME `jaa-x7k2` → `job-apply-agent-staging.web.app` at Namecheap; nothing else on nisarworks.com changed |

## How it stays private

- Sign-up is invite-only (`SIGNUP_ALLOWLIST`, currently the job mailbox only); everyone else gets 403.
- Search engines are kept out three ways: `X-Robots-Tag` on every response, a robots meta tag, and `robots.txt`.
- `APP_ENV=staging`, so every application is redirected to `SANDBOX_RECIPIENT` (the Hotmail inbox). Nothing reaches an employer.
- Gmail sign-in uses the project's own OAuth client in Testing mode; only listed test users can connect.

## What runs where

| Part | Service |
| --- | --- |
| Website (Next.js) | Cloud Run `jaa-web`, served through Firebase Hosting |
| API (FastAPI + Chromium for forms) | Cloud Run `jaa-api` (`/api/**` through Hosting) |
| Database | Cloud SQL Postgres 16, `db-f1-micro`, instance `jaa-db` |
| Locks, rate limits, page cache | Postgres (`REDIS_URL=postgres`, see `backend/app/kv.py`); no Redis server |
| CV files | Cloud Storage bucket `job-apply-agent-staging-files`, mounted at `/data` |
| Background work | Cloud Scheduler → `POST /api/system/run/{tick,allocate,renew-watches,digest}` with `X-Scheduler-Token` |
| Secrets | Secret Manager (database addresses, keys, Gemini key, OAuth client secret) |

## Deploying

Every push to `main` runs CI; when it passes, `.github/workflows/deploy-staging.yml` builds both images, runs the
database migrations as a Cloud Run job, deploys both services, refreshes the schedules, publishes Hosting and
checks `/api/health`. GitHub signs in to Google Cloud without a stored key (Workload Identity Federation,
limited to this repository's `main`). Manual run: Actions → Deploy staging → Run workflow.

`deploy/staging/setup.ps1` created the project's resources once and is safe to run again.

## Costs to expect

About US$10–15 a month, almost all of it the database. Cloud Run scales to zero between requests.
