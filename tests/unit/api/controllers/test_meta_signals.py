"""Tests for the ``GET /meta/signals`` endpoint.

The endpoint reflects each signal domain's real availability from the wired
:class:`SignalsService` (``scaling`` degrades to ``unavailable`` when no
scaling service is wired) and 503s when the service is absent, rather than
reporting a blanket ``available`` placeholder.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.meta.signals.service import SignalsService
from synthorg.meta.state import MetaStateSlice
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_URL = "/api/v1/meta/signals"
_HEADERS = make_auth_headers("ceo")


class TestGetSignals:
    async def test_returns_503_when_signals_not_wired(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        app_state = async_test_client.app.state.app_state
        original_slice = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, signals_service=None)
        try:
            resp = await async_test_client.get(_URL, headers=_HEADERS)
            assert resp.status_code == 503
            assert resp.json()["success"] is False
        finally:
            app_state.swap_slice(original_slice)

    async def test_reports_real_domain_availability(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.domain_availability.return_value = {
            "performance": True,
            "budget": True,
            "coordination": True,
            "scaling": False,
            "errors": True,
            "evolution": True,
            "telemetry": True,
        }
        app_state = async_test_client.app.state.app_state
        original_slice = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, signals_service=signals_mock)
        try:
            resp = await async_test_client.get(_URL, headers=_HEADERS)
            assert resp.status_code == 200
            domains = resp.json()["data"]["domains"]
            status_by_name = {d["name"]: d["status"] for d in domains}
            assert status_by_name["scaling"] == "unavailable"
            assert status_by_name["performance"] == "available"
            assert status_by_name["telemetry"] == "available"
        finally:
            app_state.swap_slice(original_slice)
