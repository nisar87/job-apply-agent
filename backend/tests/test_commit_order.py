"""A change must be committed before its response leaves, or the next request can miss it."""

import asyncio
import json

from sqlalchemy import select

from app.db import system_session
from app.models import Preference


def test_a_write_is_committed_before_the_response_is_sent(client, user):
    headers, _ = user
    from app.api.main import app

    body = json.dumps({"text": "no agencies"}).encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/preferences", "raw_path": b"/api/preferences", "query_string": b"", "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                    (b"authorization", headers["Authorization"].encode())],
        "client": ("testclient", 1), "server": ("testserver", 80),
    }
    seen = {}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            seen["status"] = message["status"]
            # Another connection, at the moment the first byte of the answer leaves.
            with system_session() as s:
                seen["rows"] = len(s.execute(select(Preference)).scalars().all())

    asyncio.run(app(scope, receive, send))
    assert seen == {"status": 200, "rows": 1} or seen == {"status": 201, "rows": 1}


def test_every_route_uses_the_committing_scope():
    import pathlib
    import re

    for path in pathlib.Path("app/api").glob("*.py"):
        assert not re.search(r"Depends\(db\)", path.read_text()), f"{path} uses Depends(db) without scope"
