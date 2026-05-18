"""Empty-company task submission rejection (provider-present switch).

With no LLM provider configured the company is empty: ``POST /tasks``
must be rejected with a clear 409 message instead of creating a task
that can never execute. With a provider present, creation succeeds.
"""

from collections.abc import Generator
from typing import Any

import pytest
from litestar.testing import TestClient

from tests.integration.api.conftest import build_runtime_app
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

pytestmark = pytest.mark.integration

_COMPANY_NAME = "empty-company-test"
_USERNAME = "admin"
_PASSWORD = "secure-pass-12chars"


def _extract_auth_cookies(resp: Any) -> tuple[str, str]:
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


def _authed(app: Any) -> Generator[TestClient[Any]]:
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/setup",
            json={"username": _USERNAME, "password": _PASSWORD},
        )
        assert resp.status_code == 201, resp.text
        session_token, csrf_token = _extract_auth_cookies(resp)
        client.headers["Cookie"] = f"session={session_token}; csrf_token={csrf_token}"
        client.headers["X-CSRF-Token"] = csrf_token
        yield client


@pytest.fixture
def empty_company_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> Generator[TestClient[Any]]:
    yield from _authed(
        build_runtime_app(
            fake_persistence,
            fake_message_bus,
            with_provider=False,
            company_name=_COMPANY_NAME,
        )
    )


@pytest.fixture
def provider_company_client(
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
) -> Generator[TestClient[Any]]:
    yield from _authed(
        build_runtime_app(
            fake_persistence,
            fake_message_bus,
            with_provider=True,
            company_name=_COMPANY_NAME,
        )
    )


def _task_payload() -> dict[str, Any]:
    return {
        "title": "Build the thing",
        "description": "A task that needs an agent to run.",
        "type": "development",
        "project": "proj-1",
        "created_by": _USERNAME,
    }


class TestEmptyCompanyRejectsTaskCreation:
    def test_no_provider_rejects_with_clear_message(
        self,
        empty_company_client: TestClient[Any],
    ) -> None:
        resp = empty_company_client.post("/api/v1/tasks", json=_task_payload())
        assert resp.status_code == 409, resp.text
        detail = resp.json()["error_detail"]
        # error_code is the stable contract; the message text is a
        # secondary, human-facing check against the structured field
        # (not the whole HTTP body, which could match incidentally).
        assert detail["error_code"] == 4014
        message = (detail.get("detail") or detail.get("error_message") or "").lower()
        assert "provider" in message
        assert "empty mode" in message

    def test_provider_present_allows_creation(
        self,
        provider_company_client: TestClient[Any],
    ) -> None:
        resp = provider_company_client.post("/api/v1/tasks", json=_task_payload())
        assert resp.status_code == 201, resp.text
