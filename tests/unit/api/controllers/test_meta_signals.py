"""Tests for the ``GET /meta/signals`` endpoint.

The endpoint renders each signal domain's availability from the wired
:class:`SignalsService`, and 503s when the service is absent rather than
reporting a blanket ``available`` placeholder.

What the real service answers is
:class:`~tests.unit.meta.signals.test_service.TestSignalsServiceAvailability`'s
question, not this module's: every domain aggregates from an always-wired
source, so all six read available today. Here the service is a double and the
subject is the mapping around it, which is why the domain names are taken from
the service's own tuple rather than written out again: a hand-copied list is
how a controller test comes to assert a rendering for a domain that no longer
exists.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.meta.signals.service import _SIGNAL_DOMAINS, SignalsService
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

    async def test_renders_every_domain_the_service_reports(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """Every domain arrives, under its own name, none invented or dropped."""
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.domain_availability.return_value = dict.fromkeys(
            _SIGNAL_DOMAINS, True
        )
        app_state = async_test_client.app.state.app_state
        original_slice = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, signals_service=signals_mock)
        try:
            resp = await async_test_client.get(_URL, headers=_HEADERS)
            assert resp.status_code == 200
            domains = resp.json()["data"]["domains"]
            status_by_name = {d["name"]: d["status"] for d in domains}
        finally:
            app_state.swap_slice(original_slice)
        assert status_by_name == dict.fromkeys(_SIGNAL_DOMAINS, "available")

    async def test_a_false_reading_renders_unavailable(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        """The other half of the mapping.

        No shipped domain reads ``False`` today, so this exercises the
        controller's branch rather than a reachable condition: the endpoint
        must report what the service says, not assume the answer.
        """
        degraded, *rest = _SIGNAL_DOMAINS
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.domain_availability.return_value = {
            degraded: False,
            **dict.fromkeys(rest, True),
        }
        app_state = async_test_client.app.state.app_state
        original_slice = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, signals_service=signals_mock)
        try:
            resp = await async_test_client.get(_URL, headers=_HEADERS)
            assert resp.status_code == 200
            domains = resp.json()["data"]["domains"]
            status_by_name = {d["name"]: d["status"] for d in domains}
        finally:
            app_state.swap_slice(original_slice)
        assert status_by_name[degraded] == "unavailable"
        assert all(status_by_name[name] == "available" for name in rest)
