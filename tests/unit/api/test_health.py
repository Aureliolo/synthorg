"""Tests for the liveness (/healthz) and readiness (/readyz) endpoints."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from litestar.testing import TestClient

from synthorg.api.app import create_app
from synthorg.api.controllers.health import (
    TelemetryStatus,
    _resolve_telemetry_status,
)
from synthorg.api.state import AppState
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderHealthTracker,
)
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend


@pytest.mark.unit
class TestLiveness:
    """``/healthz`` always reports ok while the event loop is responsive."""

    def test_liveness_returns_ok(self, test_client: TestClient[Any]) -> None:
        response = test_client.get("/api/v1/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["status"] == "ok"
        assert "version" in body["data"]
        assert body["data"]["uptime_seconds"] >= 0

    def test_liveness_ignores_bus_down(
        self,
        test_client: TestClient[Any],
        fake_message_bus: Any,
    ) -> None:
        # Liveness is a proof-of-life for supervisors; it does not probe
        # dependencies, so a dead bus doesn't flip it to 503.
        fake_message_bus._running = False
        response = test_client.get("/api/v1/healthz")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "ok"

    def test_old_health_endpoint_is_gone(
        self,
        test_client: TestClient[Any],
    ) -> None:
        # Pre-alpha: /health was replaced by /healthz + /readyz without
        # a compatibility shim.  Old callers must migrate.
        assert test_client.get("/api/v1/health").status_code == 404


@pytest.mark.unit
class TestReadinessHealthy:
    """``/readyz`` returns 200 when persistence + bus are both healthy."""

    def test_returns_ok_when_all_healthy(self, test_client: TestClient[Any]) -> None:
        response = test_client.get("/api/v1/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["status"] == "ok"
        assert body["data"]["persistence"] is True
        assert body["data"]["message_bus"] is True
        assert body["data"]["telemetry"] in {"enabled", "disabled"}


@pytest.mark.unit
class TestReadinessUnhealthy:
    """``/readyz`` returns 503 when any configured dependency is unhealthy."""

    def test_503_when_bus_down(
        self,
        test_client: TestClient[Any],
        fake_message_bus: Any,
    ) -> None:
        fake_message_bus._running = False
        response = test_client.get("/api/v1/readyz")
        assert response.status_code == 503
        body = response.json()
        assert body["data"]["status"] == "unavailable"
        assert body["data"]["message_bus"] is False

    def test_503_when_persistence_and_bus_down(
        self,
        test_client: TestClient[Any],
        fake_persistence: Any,
        fake_message_bus: Any,
    ) -> None:
        fake_persistence._connected = False
        fake_message_bus._running = False
        response = test_client.get("/api/v1/readyz")
        assert response.status_code == 503
        assert response.json()["data"]["status"] == "unavailable"


@pytest.mark.unit
class TestReadinessUnconfigured:
    """Dev stacks without a bus still report ready (no configured deps fail)."""

    @pytest.mark.parametrize(
        (
            "persistence_state",
            "bus_state",
            "expected_status_code",
            "expected_outcome",
            "expected_persistence",
            "expected_bus",
        ),
        [
            pytest.param(None, None, 200, "ok", None, True, id="no_services"),
            pytest.param(
                "healthy", None, 200, "ok", True, True, id="persistence_only_healthy"
            ),
            pytest.param(
                "unhealthy",
                None,
                503,
                "unavailable",
                False,
                True,
                id="persistence_only_unhealthy",
            ),
            pytest.param(None, "healthy", 200, "ok", None, True, id="bus_only_healthy"),
            pytest.param(
                None,
                "unhealthy",
                503,
                "unavailable",
                None,
                False,
                id="bus_only_unhealthy",
            ),
        ],
    )
    async def test_unconfigured_services(  # noqa: PLR0913 -- parametrized test
        self,
        persistence_state: str | None,
        bus_state: str | None,
        expected_status_code: int,
        expected_outcome: str,
        expected_persistence: bool | None,
        expected_bus: bool | None,
    ) -> None:
        backend = None
        bus = None
        if persistence_state is not None:
            backend = FakePersistenceBackend()
            await backend.connect()
        if bus_state is not None:
            bus = FakeMessageBus()
            await bus.start()

        with TestClient(
            create_app(persistence=backend, message_bus=bus),
        ) as client:
            if persistence_state == "unhealthy" and backend is not None:
                backend._connected = False
            if bus_state == "unhealthy" and bus is not None:
                bus._running = False

            response = client.get("/api/v1/readyz")
            assert response.status_code == expected_status_code
            body = response.json()
            assert body["data"]["status"] == expected_outcome
            assert body["data"]["persistence"] is expected_persistence
            assert body["data"]["message_bus"] is expected_bus


@pytest.mark.unit
class TestReadinessExceptionPaths:
    """``/readyz`` surfaces 503 when a probe raises."""

    @pytest.mark.parametrize(
        ("service_spec", "response_key"),
        [
            pytest.param(
                {
                    "factory": FakePersistenceBackend,
                    "init": "connect",
                    "kwarg": "persistence",
                    "attr": "health_check",
                    "patch_kw": {},
                },
                "persistence",
                id="persistence_exception",
            ),
            pytest.param(
                {
                    "factory": FakeMessageBus,
                    "init": "start",
                    "kwarg": "message_bus",
                    "attr": "health_check",
                    "patch_kw": {},
                },
                "message_bus",
                id="message_bus_exception",
            ),
        ],
    )
    async def test_service_exception_returns_false(
        self,
        service_spec: dict[str, Any],
        response_key: str,
    ) -> None:
        service = service_spec["factory"]()
        await getattr(service, service_spec["init"])()
        with (
            TestClient(
                create_app(**{service_spec["kwarg"]: service}),
            ) as client,
            patch.object(
                type(service),
                service_spec["attr"],
                side_effect=RuntimeError("test error"),
                **service_spec["patch_kw"],
            ),
        ):
            response = client.get("/api/v1/readyz")
            assert response.status_code == 503
            body = response.json()
            assert body["data"][response_key] is False
            assert body["data"]["status"] == "unavailable"


@pytest.mark.unit
class TestResolveTelemetryStatus:
    """Branch coverage for the health controller helper."""

    def test_disabled_when_no_collector(self) -> None:
        app_state = MagicMock(spec=AppState)
        app_state.slice.return_value = SimpleNamespace(collector=None)
        assert _resolve_telemetry_status(app_state) is TelemetryStatus.DISABLED

    def test_enabled_when_collector_is_functional(self) -> None:
        app_state = MagicMock(spec=AppState)
        app_state.slice.return_value = SimpleNamespace(
            collector=SimpleNamespace(is_functional=True)
        )
        assert _resolve_telemetry_status(app_state) is TelemetryStatus.ENABLED

    def test_disabled_when_collector_opted_out(self) -> None:
        app_state = MagicMock(spec=AppState)
        app_state.slice.return_value = SimpleNamespace(
            collector=SimpleNamespace(is_functional=False)
        )
        assert _resolve_telemetry_status(app_state) is TelemetryStatus.DISABLED

    def test_disabled_when_enabled_but_reporter_is_noop(self) -> None:
        """Enabled config + noop reporter must surface as ``disabled``."""
        app_state = MagicMock(spec=AppState)
        app_state.slice.return_value = SimpleNamespace(
            collector=SimpleNamespace(enabled=True, is_functional=False)
        )
        assert _resolve_telemetry_status(app_state) is TelemetryStatus.DISABLED


@pytest.mark.unit
class TestReadinessTelemetryField:
    """``/readyz`` always surfaces a telemetry status."""

    def test_disabled_by_default(self, test_client: TestClient[Any]) -> None:
        response = test_client.get("/api/v1/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["telemetry"] == "disabled"


@pytest.mark.unit
class TestReadinessProviders:
    """``/readyz`` reports 503 when any provider is in DOWN state.

    DEGRADED and UNKNOWN providers stay reachable; only DOWN providers
    flip the gate. Wired through ``ProviderHealthTracker.are_all_reachable``
    so the readyz probe surfaces the same view operators see in the
    provider health summary.
    """

    def test_empty_tracker_reports_providers_reachable(self) -> None:
        tracker = ProviderHealthTracker()
        with TestClient(
            create_app(provider_health_tracker=tracker),
        ) as client:
            response = client.get("/api/v1/readyz")
            assert response.status_code == 200
            body = response.json()
            assert body["data"]["status"] == "ok"
            assert body["data"]["providers"] is True

    async def test_down_provider_flips_readiness_to_503(self) -> None:
        tracker = ProviderHealthTracker()
        # Record 6 failures, 0 successes for a 100% error rate ->
        # DOWN ( >= 50% threshold).
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
        with TestClient(
            create_app(provider_health_tracker=tracker),
        ) as client:
            response = client.get("/api/v1/readyz")
            assert response.status_code == 503
            body = response.json()
            assert body["data"]["status"] == "unavailable"
            assert body["data"]["providers"] is False

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
        with TestClient(
            create_app(provider_health_tracker=tracker),
        ) as client:
            response = client.get("/api/v1/readyz")
            assert response.status_code == 200
            body = response.json()
            assert body["data"]["status"] == "ok"
            assert body["data"]["providers"] is True
