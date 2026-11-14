FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY migrations ./migrations
# WITH_BROWSER=1 adds Chromium for web application forms (phase 3); 0 gives a smaller image without them.
ARG WITH_BROWSER=1
RUN if [ "$WITH_BROWSER" = "1" ]; then \
      pip install ".[forms]" && python -m playwright install --with-deps chromium; \
    else pip install .; fi
# API:        uvicorn app.api.main:app --host 0.0.0.0 --port $PORT
# Migrations: python -m app.migrate   (run as a one-off job with the owner role's address)
# Workers:    celery -A app.tasks.celery_app worker -B --loglevel=info   (not needed with Cloud Scheduler)
CMD ["sh", "-c", "uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
