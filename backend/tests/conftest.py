import os

os.environ.setdefault("APP_ENV", "test")
os.environ["DATABASE_URL"] = "postgresql+psycopg://jobagent_app:app@localhost:5432/jobagent_test"
os.environ["DATABASE_OWNER_URL"] = "postgresql+psycopg://jobagent_owner:owner@localhost:5432/jobagent_test"
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/9")
os.environ["LLM_PROVIDER"] = "fake"
os.environ["MAIL_PROVIDER"] = "fake"
os.environ["TASKS_INLINE"] = "true"
os.environ["SANDBOX_RECIPIENT"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import reset_engine  # noqa: E402
from app.llm import set_llm_override  # noqa: E402
from app.llm.fake import FakeLLM  # noqa: E402
from app.schema import create_schema  # noqa: E402
from app.tasks.locks import get_redis  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_state():
    get_settings.cache_clear()
    reset_engine()
    create_schema(drop_first=True)
    get_redis().flushdb()
    yield
    set_llm_override(None)


@pytest.fixture
def llm():
    fake = FakeLLM()
    set_llm_override(fake)
    return fake


@pytest.fixture
def client():
    from app.api.main import app

    return TestClient(app)


def make_user(client, email="a@example.com", password="correct horse battery", name="Alex Example"):
    r = client.post("/api/auth/signup", json={"email": email, "password": password, "name": name})
    assert r.status_code == 201, r.text
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers).json()
    return headers, me["id"]


@pytest.fixture
def user(client):
    return make_user(client)


@pytest.fixture
def other_user(client):
    return make_user(client, email="b@example.com", name="Blake Other")
