"""Endpoint-level tests for the SSRF-violation review controller."""

import pytest

from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_BASE = "/api/v1/providers/ssrf-violations"
_GHOST = "00000000-0000-0000-0000-0000000000ff"


class TestSsrfViolationController:
    async def test_resolve_requires_manager_role(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            f"{_BASE}/{_GHOST}/resolve",
            headers=make_auth_headers("observer"),
            json={"status": "allowed"},
        )
        assert resp.status_code == 403

    async def test_resolve_pending_status_rejected(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        # The request DTO constrains status to allowed/denied, so a
        # 'pending' resolution is rejected at the boundary (client error)
        # before the controller body runs.
        resp = await async_test_client.post(
            f"{_BASE}/{_GHOST}/resolve",
            headers=make_auth_headers("ceo"),
            json={"status": "pending"},
        )
        assert resp.status_code in (400, 422)

    async def test_resolve_missing_violation_returns_404(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.post(
            f"{_BASE}/{_GHOST}/resolve",
            headers=make_auth_headers("ceo"),
            json={"status": "allowed"},
        )
        assert resp.status_code == 404
