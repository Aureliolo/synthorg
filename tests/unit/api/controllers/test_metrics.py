"""Tests for the Prometheus /metrics endpoint."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from litestar import Litestar
from litestar.datastructures import State
from prometheus_client import CollectorRegistry, Gauge, Info

from synthorg.api.controllers.metrics import MetricsController
from tests._shared import LoopAsyncClient


def _make_app(*, collector: object | None = None) -> Litestar:
    """Build a minimal Litestar app with the MetricsController."""
    mock_state = MagicMock()
    mock_state.slice.return_value = SimpleNamespace(
        prometheus_collector=collector, trace_handler=None
    )

    return Litestar(
        route_handlers=[MetricsController],
        state=State({"app_state": mock_state}),
    )


def _make_collector() -> MagicMock:
    """Build a mock PrometheusCollector."""
    registry = CollectorRegistry()
    Info("synthorg_app", "test info", registry=registry).info(
        {"version": "0.0.0-test"},
    )
    Gauge("synthorg_cost_total", "test cost", registry=registry).set(42.0)

    collector = MagicMock()
    collector.registry = registry
    collector.refresh = AsyncMock()
    return collector


@pytest.mark.unit
class TestMetricsEndpoint:
    """Tests for GET /metrics."""

    async def test_returns_200_with_correct_content_type(self) -> None:
        collector = _make_collector()
        async with LoopAsyncClient(app=_make_app(collector=collector)) as client:
            resp = await client.get("/metrics")
            assert resp.status_code == 200
            assert "text/plain" in resp.headers["content-type"]
            assert "version=0.0.4" in resp.headers["content-type"]

    async def test_response_contains_metric_names(self) -> None:
        collector = _make_collector()
        async with LoopAsyncClient(app=_make_app(collector=collector)) as client:
            resp = await client.get("/metrics")
            body = resp.text
            assert "synthorg_app_info" in body
            assert "synthorg_cost_total" in body

    async def test_calls_refresh(self) -> None:
        collector = _make_collector()
        async with LoopAsyncClient(app=_make_app(collector=collector)) as client:
            await client.get("/metrics")
            collector.refresh.assert_awaited_once()

    async def test_returns_503_when_collector_not_configured(self) -> None:
        async with LoopAsyncClient(app=_make_app(collector=None)) as client:
            resp = await client.get("/metrics")
            assert resp.status_code == 503
            assert "No metrics collector configured" in resp.text
