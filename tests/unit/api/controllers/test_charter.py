"""Controller tests for the project-charter endpoints.

The shared API test app runs over ``FakePersistenceBackend``, whose
``backend_name`` is ``"fake"`` so ``build_charter_repository`` returns
``None`` and the charter subsystem is never wired. The endpoints must
therefore register cleanly and degrade to a 503 (service unavailable)
rather than 404 / 500, proving the controller is mounted and its
unwired guard fires.
"""

import pytest

from tests._shared import LoopAsyncClient

pytestmark = pytest.mark.unit

_SERVICE_UNAVAILABLE = 503
_BAD_REQUEST = 400


class TestCharterControllerUnwired:
    async def test_interview_returns_503_when_unwired(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/meta/charters/interview",
            json={"message": "build a better memory tool"},
        )
        assert resp.status_code == _SERVICE_UNAVAILABLE

    async def test_list_returns_503_when_unwired(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/meta/charters")
        assert resp.status_code == _SERVICE_UNAVAILABLE

    async def test_get_returns_503_when_unwired(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.get("/api/v1/meta/charters/charter-1")
        assert resp.status_code == _SERVICE_UNAVAILABLE

    async def test_approve_returns_503_when_dispatcher_unwired(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/meta/charters/charter-1/approve",
            json={},
        )
        assert resp.status_code == _SERVICE_UNAVAILABLE

    async def test_patch_returns_503_when_unwired(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.patch(
            "/api/v1/meta/charters/charter-1",
            json={"brief": "tweaked"},
        )
        assert resp.status_code == _SERVICE_UNAVAILABLE

    async def test_cancel_returns_503_when_unwired(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/meta/charters/charter-1/cancel",
            json={},
        )
        assert resp.status_code == _SERVICE_UNAVAILABLE

    async def test_interview_rejects_blank_message(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        resp = await async_test_client.post(
            "/api/v1/meta/charters/interview",
            json={"message": "   "},
        )
        assert resp.status_code == _BAD_REQUEST
