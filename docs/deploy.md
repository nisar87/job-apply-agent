# Deployment on Google Cloud and Firebase

A Firebase project is a Google Cloud project, so everything lives in one place.

| Part | Service | Notes |
| --- | --- | --- |
| Web app | Firebase App Hosting, or Cloud Run with `frontend/Dockerfile` | Set `NEXT_PUBLIC_API_URL` to the API's address at build time |
| API | Cloud Run with `backend/Dockerfile` | Region `europe-west3` (Frankfurt) |
| Workers | A second Cloud Run service or a small VM running `celery -A app.tasks.celery_app worker -B` | Needs to stay up; set `TASKS_INLINE=false` on the API |
| Database | Cloud SQL for PostgreSQL 16 | Create the two roles from `scripts/init-db.sql`, then run `python -m app.schema` once as the owner |
| Queue and locks | Memorystore for Redis | |
| Files | Cloud Storage bucket mounted at `STORAGE_DIR` | Private bucket, Frankfurt |
| Secrets | Secret Manager | Every value from `.env.example`; never bake them into images |
| Gmail push | Pub/Sub topic with a push subscription to `/api/mail/google/push?token=...` | Set `GOOGLE_PUBSUB_TOPIC` and `PUBSUB_VERIFICATION_TOKEN`; grant `gmail-api-push@system.gserviceaccount.com` the Publisher role on the topic |
| Notice emails | Any SMTP service on your own domain | `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFY_FROM`; set up SPF, DKIM and DMARC |

Required in production: `APP_ENV=production`, a long random `SECRET_KEY`, a generated
`TOKEN_ENCRYPTION_KEY`, `FRONTEND_URL` set to the web app's address.

## Schema changes

`python -m app.schema` creates missing tables and re-applies the row-level security
policies. It does not alter existing columns. Before the first deployment with real
users, generate a baseline with Alembic (`alembic revision --autogenerate`) and use
migrations from then on.

## Not done by this repository

- Google's verification of the Gmail scopes and the yearly security assessment.
- An external penetration test and a lawyer's review of the legal documents.
- Billing. Plans exist only as a daily model-spend cap and a daily send limit per user.
