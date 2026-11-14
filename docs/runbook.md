# Runbook

The operator console (`/operator`, for accounts with `is_operator = true`) shows users,
connection health, failed runs and model spend. `/api/operator/health` lists open alerts.

| Situation | What to do |
| --- | --- |
| Something is being sent that should not be | Operator console: "Pause sending for everyone". This stops every send and the scheduler at once. Then investigate with the audit log. |
| One user's account misbehaves | Operator console: pause that user. Their searches are paused too. |
| Gmail is down or rejects tokens | Nothing to do: sends are retried in the next window, and a user whose token is rejected is asked to reconnect. Do not raise limits afterwards to "catch up". |
| Gemini is down | Runs fail and are retried by the next scheduler tick. Checks are never skipped: no draft is sent without its fact check. |
| A release is bad | Pause sending for everyone, roll the API and workers back to the previous image, resume. |
| An application is stuck in "sending" | It means the process died mid-send. After 10 minutes it is marked "needs attention" and is never re-sent automatically. Ask the user to check their Sent folder. |
| An employer complains | Add their address and domain on the operator console's opt-out list (or approve their request from the public form). The block is permanent and applies to every user. Reply to the employer confirming it. |
| Alert `watch_expiring` | The daily renewal failed. Run the `jobs.renew_mail_watches` task by hand and check Pub/Sub permissions. The 30-minute safety poll keeps replies flowing meanwhile. |
| Alert `bounce_rate` | The user's campaigns should be paused and their sources checked: bounces mean bad contact addresses. |
| Alert `spend_cap_reached` | Expected protection. Raise `LLM_DAILY_SPEND_CAP_USD` only after checking why that user's usage is high. |

## Routines

- Weekly: read a sample of sent emails and automatic replies.
- Monthly: re-check each job source's terms (see `docs/sources.md`) and Google's developer documentation.
- Yearly: Google re-verification and security assessment.
