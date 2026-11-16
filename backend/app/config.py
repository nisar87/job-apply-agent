"""Application settings, read from environment variables or a local .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_env: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://jobagent_app:app@localhost:5432/jobagent"
    database_owner_url: str = "postgresql+psycopg://jobagent_owner:owner@localhost:5432/jobagent"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-secret-key-change-me-please-32chars"
    token_encryption_key: str = ""
    access_token_minutes: int = 60 * 24 * 7
    frontend_url: str = "http://localhost:3000"
    storage_dir: str = "storage"
    # Run background tasks inside the calling process (development and tests).
    tasks_inline: bool = True

    # LLM. Model names are settings, never code (master document, section 3).
    llm_provider: str = "gemini"  # gemini | fake
    gemini_api_key: str = ""
    gemini_model_fast: str = "gemini-3.5-flash-lite"
    gemini_model_strong: str = "gemini-3.8-flash"
    gemini_model_embed: str = "gemini-embedding-001"
    # US dollars per million tokens: {model: [input, output]}. Used for cost recording only.
    llm_prices: dict[str, list[float]] = {
        "gemini-3.5-flash-lite": [0.30, 2.50],
        "gemini-3.8-flash": [1.50, 7.50],
    }
    llm_daily_spend_cap_usd: float = 1.0

    # Google OAuth / Gmail
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/mail/google/callback"
    google_pubsub_topic: str = ""
    pubsub_verification_token: str = ""
    mail_provider: str = "gmail"  # gmail | fake

    # Job sources
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    # Adzuna counts every request: its trial has 25 in total, the paid plans 500 a day and 5,000 or 20,000 a month.
    adzuna_daily_calls: int = 2
    adzuna_monthly_calls: int = 0  # 0 = no monthly cap beyond the daily one

    # The product's own email (escalations, digest, account notices). Never used for applications.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    notify_from: str = "Job Apply Agent <no-reply@example.com>"

    # Safety: outside production every outgoing application is rewritten to this address.
    sandbox_recipient: str = ""

    # Browser used for web application forms. Empty lets Playwright use its own Chromium.
    browser_executable: str = ""
    form_auto_submit_after: int = 5  # clean reviews per hiring system before automatic submission is offered

    # A private deployment (staging): only these addresses may sign up (comma separated; empty = anyone),
    # and search engines are told not to index anything.
    signup_allowlist: str = ""
    # Accounts with these addresses see the admin panel (comma separated), as well as users marked is_operator.
    admin_emails: str = ""
    hide_from_search: bool = False
    # Testing only: let the agent answer employers outside weekday working hours.
    agent_reply_any_time: bool = False
    # Run "Look for jobs now" and "Write drafts now" after the response (behind a proxy with a short request limit).
    manual_runs_in_background: bool = False
    # Shared secret Cloud Scheduler sends to /api/system/run/{job} when no Celery worker runs.
    scheduler_token: str = ""

    # Agent limits
    agent_mode: str = "llm"  # llm | playbook
    agent_max_steps: int = 40
    agent_run_timeout_seconds: int = 600

    # Global protections (master document, section 10)
    posting_cap_per_day: int = 5
    posting_cap_total: int = 20
    user_daily_send_ceiling: int = 30
    min_send_gap_minutes: int = 2
    avg_send_gap_minutes: int = 8

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def is_admin_email(self, email: str) -> bool:
        admins = {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}
        return email.strip().lower() in admins


@lru_cache
def get_settings() -> Settings:
    return Settings()
