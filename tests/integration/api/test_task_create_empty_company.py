"""Empty-company task submission rejection (provider-present switch).

With no LLM provider configured the company is empty: ``POST /tasks``
must be rejected with a clear 409 message instead of creating a task
that can never execute. With a provider present, creation succeeds.
"""

from collections.abc import AsyncGenerator

import httpx
import pytest
from litestar import Litestar

from tests._shared import LoopAsyncClient
from tests._shared.json_types import JsonDict
from tests.integration.api.conftest import build_runtime_app
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.integration

_COMPANY_NAME = "empty-company-test"
_USERNAME = "admin"
_PASSWORD = "secure-pass-12chars"


def _extract_auth_cookies(resp: httpx.Response) -> tuple[str, str]:
    session = ""
    csrf = ""
    for k, v in resp.headers.multi_items():
        if k.lower() != "set-cookie":
            continue
        if v.startswith("session="):
            session = v.split("session=")[1].split(";")[0]
        elif v.startswith("csrf_token="):
            csrf = v.split("csrf_token=")[1].split(";")[0]
    return session, csrf


async def _authed(app: Litestar) -> AsyncGenerator[LoopAsyncClient]:
    async with LoopAsyncClient(app) as client:
        resp = await client.post(
            "/api/v1/auth/setup",
            json={"username": _USERNAME, "password": _PASSWORD},
        )
        assert resp.status_code == 201, resp.text
        session_token, csrf_token = _extract_auth_cookies(resp)
        assert session_token, "Missing session cookie from /api/v1/auth/setup"
        assert csrf_token, "Missing csrf_token cookie from /api/v1/auth/setup"
        client.headers["Cookie"] = f"session={session_token}; csrf_token={csrf_token}"
        client.headers["X-CSRF-Token"] = csrf_token
        yield client


@pytest.fixture
async def empty_company_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> AsyncGenerator[LoopAsyncClient]:
    async for client in _authed(
        build_runtime_app(
            fake_persistence,
            fake_message_bus,
            with_provider=False,
            company_name=_COMPANY_NAME,
        )
    ):
        yield client


@pytest.fixture
async def provider_company_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> AsyncGenerator[LoopAsyncClient]:
    async for client in _authed(
        build_runtime_app(
            fake_persistence,
            fake_message_bus,
            with_provider=True,
            company_name=_COMPANY_NAME,
        )
    ):
        yield client


def _task_payload() -> JsonDict:
    return {
        "title": "Build the thing",
        "description": "A task that needs an agent to run.",
        "type": "development",
        "project": "proj-1",
        "created_by": _USERNAME,
    }


class TestEmptyCompanyRejectsTaskCreation:
    async def test_no_provider_rejects_with_clear_message(
        self,
        empty_company_client: LoopAsyncClient,
    ) -> None:
        resp = await empty_company_client.post("/api/v1/tasks", json=_task_payload())
        assert resp.status_code == 409, resp.text
        detail = resp.json()["error_detail"]
        # error_code is the stable contract; the message text is a
        # secondary, human-facing check against the structured field
        # (not the whole HTTP body, which could match incidentally).
        assert detail["error_code"] == 4014
        message = (detail.get("detail") or detail.get("error_message") or "").lower()
        assert "provider" in message
        assert "empty mode" in message

    async def test_provider_present_allows_creation(
        self,
        provider_company_client: LoopAsyncClient,
    ) -> None:
        """``POST /tasks`` now returns 202 + a submission envelope.

        The runtime app boots with a provider + auto-wired simulation
        runtime + work pipeline, so the boot hook attaches the
        ``TaskBoardEntryAdapter``. The board controller hands the
        filing to it and returns 202; the spine creates the task in a
        detached background coroutine.
        """
        resp = await provider_company_client.post("/api/v1/tasks", json=_task_payload())
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["title"] == "Build the thing"
        assert data["project"] == "proj-1"
        assert data["status"] == "submitted"
        assert isinstance(data["correlation_id"], str)
        assert data["correlation_id"]
