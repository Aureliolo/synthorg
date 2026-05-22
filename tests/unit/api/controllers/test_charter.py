"""Controller tests for the project-charter endpoints.

The shared API test app runs over ``FakePersistenceBackend``, whose
``backend_name`` is ``"fake"`` so ``build_charter_repository`` returns
``None`` and the charter subsystem is never wired. The endpoints must
therefore register cleanly and degrade to a 503 (service unavailable)
rather than 404 / 500, proving the controller is mounted and its
unwired guard fires.
"""

from typing import Any

import pytest
from litestar.testing import TestClient

pytestmark = pytest.mark.unit

_SERVICE_UNAVAILABLE = 503
_BAD_REQUEST = 400


class TestCharterControllerUnwired:
    def test_interview_returns_503_when_unwired(
        self, test_client: TestClient[Any]
    ) -> None:
        resp = test_client.post(
            "/api/v1/meta/charters/interview",
            json={"message": "build a better memory tool"},
        )
        assert resp.status_code == _SERVICE_UNAVAILABLE

    def test_list_returns_503_when_unwired(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/meta/charters")
        assert resp.status_code == _SERVICE_UNAVAILABLE

    def test_get_returns_503_when_unwired(self, test_client: TestClient[Any]) -> None:
        resp = test_client.get("/api/v1/meta/charters/charter-1")
        assert resp.status_code == _SERVICE_UNAVAILABLE

    def test_approve_returns_503_when_dispatcher_unwired(
        self, test_client: TestClient[Any]
    ) -> None:
        resp = test_client.post(
            "/api/v1/meta/charters/charter-1/approve",
            json={},
        )
        assert resp.status_code == _SERVICE_UNAVAILABLE

    def test_interview_rejects_blank_message(
        self, test_client: TestClient[Any]
    ) -> None:
        resp = test_client.post(
            "/api/v1/meta/charters/interview",
            json={"message": "   "},
        )
        assert resp.status_code == _BAD_REQUEST
