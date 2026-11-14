FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml ./
COPY app ./app
RUN pip install .
# API:     uvicorn app.api.main:app --host 0.0.0.0 --port 8080
# Workers: celery -A app.tasks.celery_app worker -B --loglevel=info
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
