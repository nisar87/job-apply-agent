# End-to-end smoke test

Drives the real web app in Chrome, in demo mode (no Gemini key, practice mailbox).

```bash
# terminal 1: API in demo mode
cd backend && python -m app.schema
APP_ENV=development LLM_PROVIDER=fake MAIL_PROVIDER=fake AGENT_MODE=playbook uvicorn app.api.main:app --port 8000

# terminal 2: web app
cd frontend && npm run build && npm run start

# terminal 3
pip install playwright && python e2e/smoke.py
```
