# Job Apply Agent

A multi-user web application in which each user describes their job search in plain
language. An agent then finds, scores, applies to and follows up on jobs from the user's
own Gmail account. The model provider is the Gemini API.

The full specification is the master implementation document. This repository follows
its phases.

## Layout

| Folder | What it holds |
| --- | --- |
| `backend/` | API, service layer, agent, connectors and workers (Python, FastAPI) |
| `frontend/` | Web application (Next.js) |
| `scripts/` | Database setup |
| `docs/` | Notes for operators and developers |

## Run it locally

You need Python 3.12+, Node 22+, and Docker (or your own Postgres 16 and Redis 7).

```bash
docker compose up -d                 # Postgres and Redis
cp .env.example .env                 # then fill in the values you have

cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m app.schema                 # creates tables and row-level security
uvicorn app.api.main:app --reload    # API on http://localhost:8000

cd ../frontend
npm install
npm run dev                          # web app on http://localhost:3000
```

Background work runs inside the API process by default (`TASKS_INLINE=true`). For real
schedules, set `TASKS_INLINE=false` and start a worker:

```bash
cd backend && celery -A app.tasks.celery_app worker -B --loglevel=info
```

## Keys and secrets

Secrets live in `.env`, which is never committed. Put your Gemini key in
`GEMINI_API_KEY`. To try the application without any key, set `LLM_PROVIDER=fake` and
`MAIL_PROVIDER=fake`: the model and the mailbox are then replaced by local stand-ins.

Outside production, set `SANDBOX_RECIPIENT` to your own address. Every outgoing
application is then redirected there, so a test can never reach a real employer.

## Tests

```bash
cd backend && pytest -q && ruff check .
cd frontend && npm run lint && npm run build
```

Tests need the two database roles from `scripts/init-db.sql` and a local Redis.
