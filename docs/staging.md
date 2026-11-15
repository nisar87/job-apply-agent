# Private staging

A hidden copy of the app for testing with real services before any public launch.

| | |
| --- | --- |
| Address | https://jobs.aicvstudio.app (also https://job-apply-agent-staging.web.app; the first address, jaa-x7k2.nisarworks.com, redirects here) |
| Firebase / Google Cloud project | `job-apply-agent-staging`, its own project; billing account "Firebase Payment", budget alert US$20/month |
| Owner account | noreply.dotnetobject@gmail.com |
| DNS | at Namecheap: CNAME `jobs` on aicvstudio.app and `jaa-x7k2` on nisarworks.com, both → `job-apply-agent-staging.web.app`. Nothing else on either domain changed; AI CV Studio's own site and App Hosting are untouched |

## How it stays private

- Sign-up is invite-only (`SIGNUP_ALLOWLIST`, currently the job mailbox only); everyone else gets 403.
- Search engines are kept out three ways: `X-Robots-Tag` on every response, a robots meta tag, and `robots.txt`.
- `APP_ENV=staging`, so every application is redirected to `SANDBOX_RECIPIENT` (the Hotmail inbox). Nothing reaches an employer.
- Gmail sign-in uses the project's own OAuth client in Testing mode; only listed test users can connect.

## Admin panel and AI keys

`/operator` (shown as **Admin** in the menu) is for the addresses in `ADMIN_EMAILS` and users marked `is_operator`.
It sets how Gemini keys work for everyone (`app/llm/access.py`):

- **My key for everyone**: every user runs on `GEMINI_API_KEY`, within a daily limit per user.
- **Their key, or mine if allowed** (default): a user's own key (Account → AI key, stored encrypted) when they add one,
  otherwise the operator's key for users allowed to use it.
- **Everyone brings their own key**: users without a key are stopped until they add one.

It also shows each user's spend, send limit and pause switch, and the log of web forms the agent could not fill
or submit, grouped by cause (CAPTCHA, login, missing facts, agent error…).

## Web forms

With **Fill in employers' web forms for me** ticked on a search, each drafting run fills up to 5 forms, best match
first. Each job gets two tries; failures are logged with their reason and the job stays on the apply-by-hand list.
A filled form waits for the user's approval. Staging submits only the practice forms on its own site
(`/practice/form-dotnet.html`); a real employer's form is refused there, because it cannot be sandboxed like email.

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
