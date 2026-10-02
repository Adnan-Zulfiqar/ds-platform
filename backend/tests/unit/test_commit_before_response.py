"""A request's transaction commits before its response leaves the server.

FastAPI 0.118+ runs a default ("request"-scoped) yield dependency's exit code
after the response is sent. `get_db_session` commits in that exit code, so on
the default scope a client could receive 201 for a registration whose rows
were not yet committed — the next request then failed with a foreign-key
error (the `global-rules-impact.spec.ts` flakes) — and a commit that failed
had already been reported as success.

These tests record the order of ASGI messages and session calls on a real
FastAPI app wired with the real `DbSession` and exception handlers; only the
session factory is replaced.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from app.api import deps
from app.api.deps import DbSession
from app.api.error_handlers import register_exception_handlers

pytestmark = pytest.mark.unit


class _RecordingSession:
    def __init__(self, events: list[str], *, fail_commit: bool) -> None:
        self._events = events
        self._fail_commit = fail_commit

    async def commit(self) -> None:
        if self._fail_commit:
            self._events.append("commit-failed")
            raise OperationalError("COMMIT", {}, Exception("connection lost"))
        self._events.append("commit")

    async def rollback(self) -> None:
        self._events.append("rollback")

    async def close(self) -> None:
        self._events.append("close")


def _app(events: list[str]) -> Any:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/things", status_code=201)
    async def create(_session: DbSession) -> dict[str, bool]:
        events.append("handler")
        return {"created": True}

    async def recording_asgi(scope: Any, receive: Any, send: Any) -> None:
        async def recording_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                events.append(f"response-{message['status']}")
            await send(message)

        await app(scope, receive, recording_send)

    return recording_asgi


async def _post(asgi: Any) -> httpx.Response:
    transport = httpx.ASGITransport(app=asgi, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/things")


async def test_the_commit_happens_before_the_success_response_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        deps, "session_factory", lambda: _RecordingSession(events, fail_commit=False)
    )

    response = await _post(_app(events))

    assert response.status_code == 201
    assert events.index("commit") < events.index("response-201"), events


async def test_a_failed_commit_is_never_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        deps, "session_factory", lambda: _RecordingSession(events, fail_commit=True)
    )

    response = await _post(_app(events))

    assert response.status_code >= 500, events
    assert "response-201" not in events
    assert "rollback" in events
