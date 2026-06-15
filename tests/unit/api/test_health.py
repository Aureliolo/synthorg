"""Tests for liveness (/healthz), readiness (/readyz), and health (/health).

``/readyz`` is the unauthenticated supervisor probe: it returns the
binary outcome plus version and uptime only, never the component
topology. The per-component breakdown lives behind authentication on
``/health``.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from synthorg.api.controllers.health import (
    TelemetryStatus,
    _resolve_telemetry_status,
)
from synthorg.api.state import AppState
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderHealthTracker,
)
from tests._shared import JsonDict, LoopAsyncClient
from tests._shared import build_test_app as create_app
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

_TOPOLOGY_KEYS = ("persistence", "message_bus", "providers", "telemetry")


@pytest.mark.unit
class TestLiveness:
    """``/healthz`` always reports ok while the event loop is responsive."""

    async def test_liveness_returns_ok(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.get("/api/v1/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["status"] == "ok"
        assert "version" in body["data"]
        assert body["data"]["uptime_seconds"] >= 0

    async def test_liveness_ignores_bus_down(
        self,
        async_test_client: LoopAsyncClient,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # Liveness is a proof-of-life for supervisors; it does not probe
        # dependencies, so a dead bus doesn't flip it to 503.
        fake_message_bus._running = False
        response = await async_test_client.get("/api/v1/healthz")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "ok"


@pytest.mark.unit
class TestReadinessProbe:
    """``/readyz`` returns a topology-free outcome + 200/503."""

    async def test_returns_ok_when_all_healthy(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.get("/api/v1/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["status"] == "ok"
        assert "version" in body["data"]
        assert body["data"]["uptime_seconds"] >= 0

    async def test_body_carries_no_topology(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """The unauthenticated probe must not leak operational topology."""
        response = await async_test_client.get("/api/v1/readyz")
        body = response.json()["data"]
        for key in _TOPOLOGY_KEYS:
            assert key not in body, f"/readyz must not expose {key!r}"


@pytest.mark.unit
class TestReadinessUnhealthy:
    """``/readyz`` returns 503 when any configured dependency is unhealthy."""

    async def test_503_when_bus_down(
        self,
        async_test_client: LoopAsyncClient,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        fake_message_bus._running = False
        response = await async_test_client.get("/api/v1/readyz")
        assert response.status_code == 503
        assert response.json()["data"]["status"] == "unavailable"

    async def test_503_when_persistence_and_bus_down(
        self,
        async_test_client: LoopAsyncClient,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        fake_persistence._connected = False
        fake_message_bus._running = False
        response = await async_test_client.get("/api/v1/readyz")
        assert response.status_code == 503
        assert response.json()["data"]["status"] == "unavailable"


@pytest.mark.unit
class TestReadinessUnconfigured:
    """Dev stacks without a bus still report ready (no configured deps fail).

    Asserts the gate outcome only; component values are an authenticated
    ``/health`` concern.
    """

    @pytest.mark.parametrize(
        ("persistence_state", "bus_state", "expected_status_code", "expected_outcome"),
        [
            pytest.param(None, None, 200, "ok", id="no_services"),
            pytest.param("healthy", None, 200, "ok", id="persistence_only_healthy"),
            pytest.param(
                "unhealthy", None, 503, "unavailable", id="persistence_only_unhealthy"
            ),
            pytest.param(None, "healthy", 200, "ok", id="bus_only_healthy"),
            pytest.param(
                None, "unhealthy", 503, "unavailable", id="bus_only_unhealthy"
            ),
        ],
    )
    async def test_unconfigured_services(
        self,
        persistence_state: str | None,
        bus_state: str | None,
        expected_status_code: int,
        expected_outcome: str,
    ) -> None:
        backend = None
        bus = None
        if persistence_state is not None:
            backend = FakePersistenceBackend()
            await backend.connect()
        if bus_state is not None:
            bus = FakeMessageBus()
            await bus.start()

        async with LoopAsyncClient(
            create_app(persistence=backend, message_bus=bus),
        ) as client:
            if persistence_state == "unhealthy" and backend is not None:
                backend._connected = False
            if bus_state == "unhealthy" and bus is not None:
                bus._running = False

            response = await client.get("/api/v1/readyz")
            assert response.status_code == expected_status_code
            assert response.json()["data"]["status"] == expected_outcome


@pytest.mark.unit
class TestReadinessExceptionPaths:
    """``/readyz`` surfaces 503 when a probe raises."""

    @pytest.mark.parametrize(
        "service_spec",
        [
            pytest.param(
                {
                    "factory": FakePersistenceBackend,
                    "init": "connect",
                    "kwarg": "persistence",
                    "attr": "health_check",
                },
                id="persistence_exception",
            ),
            pytest.param(
                {
                    "factory": FakeMessageBus,
                    "init": "start",
                    "kwarg": "message_bus",
                    "attr": "health_check",
                },
                id="message_bus_exception",
            ),
        ],
    )
    async def test_service_exception_returns_503(
        self,
        service_spec: JsonDict,
    ) -> None:
        service = service_spec["factory"]()
        await getattr(service, service_spec["init"])()
        async with LoopAsyncClient(
            create_app(**{service_spec["kwarg"]: service}),
        ) as client:
            with patch.object(
                type(service),
                service_spec["attr"],
                side_effect=RuntimeError("test error"),
            ):
                response = await client.get("/api/v1/readyz")
                assert response.status_code == 503
                assert response.json()["data"]["status"] == "unavailable"


@pytest.mark.unit
class TestHealthDetail:
    """``/health`` exposes the per-component breakdown behind auth."""

    async def test_rejects_invalid_token(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # An invalid bearer token overrides the shared client's default
        # CEO header and must be rejected: ``/health`` is auth-gated,
        # unlike the public ``/readyz`` probe.
        response = await async_test_client.get(
            "/api/v1/health",
            headers={"Authorization": "Bearer not.a.valid.token"},
        )
        assert response.status_code in {401, 403}

    async def test_authenticated_returns_component_breakdown(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # ``async_test_client`` carries a default CEO Authorization header.
        response = await async_test_client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["status"] == "ok"
        assert body["persistence"] is True
        assert body["message_bus"] is True
        # ``/health`` exposes the full per-component breakdown, so the
        # providers component must be present (None when unconfigured).
        assert "providers" in body
        assert body["telemetry"] in {"enabled", "disabled"}

    async def test_authenticated_503_when_bus_down(
        self,
        async_test_client: LoopAsyncClient,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        fake_message_bus._running = False
        response = await async_test_client.get("/api/v1/health")
        assert response.status_code == 503
        body = response.json()["data"]
        assert body["status"] == "unavailable"
        assert body["message_bus"] is False


@pytest.mark.unit
class TestResolveTelemetryStatus:
    """Branch coverage for the health controller helper."""

    async def test_disabled_when_no_collector(self) -> None:
        app_state = MagicMock(spec=AppState)
        app_state.slice.return_value = SimpleNamespace(collector=None)
        assert _resolve_telemetry_status(app_state) is TelemetryStatus.DISABLED

    async def test_enabled_when_collector_is_functional(self) -> None:
        app_state = MagicMock(spec=AppState)
        app_state.slice.return_value = SimpleNamespace(
            collector=SimpleNamespace(is_functional=True)
        )
        assert _resolve_telemetry_status(app_state) is TelemetryStatus.ENABLED

    async def test_disabled_when_collector_opted_out(self) -> None:
        app_state = MagicMock(spec=AppState)
        app_state.slice.return_value = SimpleNamespace(
            collector=SimpleNamespace(is_functional=False)
        )
        assert _resolve_telemetry_status(app_state) is TelemetryStatus.DISABLED

    async def test_disabled_when_enabled_but_reporter_is_noop(self) -> None:
        """Enabled config + noop reporter must surface as ``disabled``."""
        app_state = MagicMock(spec=AppState)
        app_state.slice.return_value = SimpleNamespace(
            collector=SimpleNamespace(enabled=True, is_functional=False)
        )
        assert _resolve_telemetry_status(app_state) is TelemetryStatus.DISABLED


@pytest.mark.unit
class TestReadinessProviders:
    """``/readyz`` reports 503 when any provider is in DOWN state.

    DEGRADED and UNKNOWN providers stay reachable; only DOWN providers
    flip the gate. Wired through ``ProviderHealthTracker.are_all_reachable``.
    Asserts the gate outcome only; the ``providers`` component value is an
    authenticated ``/health`` concern.
    """

    async def test_empty_tracker_reports_ready(self) -> None:
        tracker = ProviderHealthTracker()
        async with LoopAsyncClient(
            create_app(provider_health_tracker=tracker),
        ) as client:
            response = await client.get("/api/v1/readyz")
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "ok"

    async def test_down_provider_flips_readiness_to_503(self) -> None:
        tracker = ProviderHealthTracker()
        # 6 failures, 0 successes => 100% error rate => DOWN (>=50%).
        now = datetime.now(UTC)
        for i in range(6):
            await tracker.record(
                ProviderHealthRecord(
                    provider_name="example-provider",
                    timestamp=now,
                    success=False,
                    response_time_ms=120.0,
                    error_message=f"simulated failure {i}",
                ),
            )
        async with LoopAsyncClient(
            create_app(provider_health_tracker=tracker),
        ) as client:
            response = await client.get("/api/v1/readyz")
            assert response.status_code == 503
            assert response.json()["data"]["status"] == "unavailable"

    async def test_degraded_provider_stays_reachable(self) -> None:
        tracker = ProviderHealthTracker()
        # 2 failures + 8 successes => 20% error rate => DEGRADED, not DOWN.
        now = datetime.now(UTC)
        for i in range(2):
            await tracker.record(
                ProviderHealthRecord(
                    provider_name="example-provider",
                    timestamp=now,
                    success=False,
                    response_time_ms=120.0,
                    error_message=f"simulated failure {i}",
                ),
            )
        for _ in range(8):
            await tracker.record(
                ProviderHealthRecord(
                    provider_name="example-provider",
                    timestamp=now,
                    success=True,
                    response_time_ms=80.0,
                ),
            )
        async with LoopAsyncClient(
            create_app(provider_health_tracker=tracker),
        ) as client:
            response = await client.get("/api/v1/readyz")
            assert response.status_code == 200
            assert response.json()["data"]["status"] == "ok"
