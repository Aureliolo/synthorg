"""Tests for liveness (/healthz), readiness (/readyz), and health (/health).

``/readyz`` is the unauthenticated supervisor probe: it returns the
binary outcome plus version and uptime only, never the component
topology. The per-component breakdown lives behind authentication on
``/health``.
"""

from contextlib import AbstractContextManager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthorg.api.controllers._memory_health import MemoryHealth, MemoryState
from synthorg.api.controllers.health import (
    TelemetryStatus,
    _memory_readiness,
    _probe_persistence,
    _resolve_memory_health,
    _resolve_telemetry_status,
)
from synthorg.api.state import AppState
from synthorg.memory.protocol import MemoryBackend
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderHealthTracker,
)
from tests._shared import JsonDict, LoopAsyncClient, mock_of
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
class TestReadinessMemoryOverHttp:
    """A degraded memory keeps ``/readyz`` on the value the CLI waits for.

    ``synthorg start`` polls ``/readyz`` and completes only on the literal
    ``"ok"``; anything else it keeps polling until the timeout and then
    fails the start. So an embedder above the index ceiling, which is
    DEGRADED (correct results, exact scan), has to reach the wire as
    ``ok``/200 rather than merely abstain from the readiness verdict in
    a helper. Asserted through HTTP because the abstention only becomes
    ``ok`` after the aggregation drops it.
    """

    @staticmethod
    def _patch_memory(state: MemoryState) -> AbstractContextManager[AsyncMock]:
        return patch(
            "synthorg.api.controllers.health._resolve_memory_health",
            AsyncMock(
                spec=_resolve_memory_health,
                return_value=MemoryHealth(state=state, backend="sqlvector"),
            ),
        )

    async def test_degraded_memory_stays_ready(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        with self._patch_memory(MemoryState.DEGRADED):
            response = await async_test_client.get("/api/v1/readyz")
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "ok"

    async def test_unreachable_memory_gates_traffic(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        with self._patch_memory(MemoryState.UNREACHABLE):
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
class TestMemoryHealth:
    """Memory that never wired must be visible, not silently absent.

    An operator whose embedder failed to resolve previously saw a
    healthy system that simply never remembered anything.
    """

    async def test_unwired_memory_reports_off_with_a_remedy(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.get("/api/v1/health")

        memory = response.json()["data"]["memory"]

        assert memory["state"] == "off"
        assert "embedder" in (memory["detail"] or "")

    async def test_state_names_the_configured_backend(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        response = await async_test_client.get("/api/v1/health")

        assert response.json()["data"]["memory"]["backend"] == "sqlvector"


@pytest.mark.unit
class TestResolveMemoryHealth:
    """``_resolve_memory_health`` probes the live backend, not just wiring.

    A wired backend can still have lost its store or its dense index
    after boot; keyword-only recall answers every query, so it reads as
    working memory unless the health surface probes for it.
    """

    def _app_state(
        self,
        *,
        backend: object,
        scheduler: object = object(),
        configured: str = "sqlvector",
        embedder_ref: str | None = "test-provider/embed-model",
    ) -> AppState:
        app_state = MagicMock(spec=AppState)
        app_state.config = SimpleNamespace(memory=SimpleNamespace(backend=configured))
        app_state.slice.return_value = SimpleNamespace(
            backend=backend,
            consolidation_scheduler=scheduler,
            embedder_ref=embedder_ref,
        )
        return app_state

    @staticmethod
    def _backend(*, healthy: bool, dense: bool) -> object:
        backend = mock_of[MemoryBackend](supports_dense_search=dense)
        backend.health_check = AsyncMock(
            spec=MemoryBackend.health_check, return_value=healthy
        )
        return backend

    async def test_healthy_dense_backend_with_scheduler_is_durable(self) -> None:
        app_state = self._app_state(backend=self._backend(healthy=True, dense=True))
        result = await _resolve_memory_health(app_state)
        assert result.state is MemoryState.DURABLE

    async def test_failed_probe_is_unreachable(self) -> None:
        # Distinct from DEGRADED: reads and writes are failing, which is the
        # one memory condition that gates traffic.
        app_state = self._app_state(backend=self._backend(healthy=False, dense=True))
        result = await _resolve_memory_health(app_state)
        assert result.state is MemoryState.UNREACHABLE
        assert result.detail is not None

    async def test_builtin_embedder_is_degraded(self) -> None:
        app_state = self._app_state(
            backend=self._backend(healthy=True, dense=True),
            embedder_ref="builtin/hashing",
        )
        result = await _resolve_memory_health(app_state)
        assert result.state is MemoryState.DEGRADED
        assert "built-in embedder" in (result.detail or "")

    async def test_lexical_only_backend_is_degraded(self) -> None:
        # Recall answers every query on keyword matches, so a lost dense
        # index must surface rather than read as healthy.
        app_state = self._app_state(backend=self._backend(healthy=True, dense=False))
        result = await _resolve_memory_health(app_state)
        assert result.state is MemoryState.DEGRADED
        assert "keyword-only" in (result.detail or "")

    async def test_missing_scheduler_is_degraded(self) -> None:
        app_state = self._app_state(
            backend=self._backend(healthy=True, dense=True), scheduler=None
        )
        result = await _resolve_memory_health(app_state)
        assert result.state is MemoryState.DEGRADED
        assert "maintenance" in (result.detail or "")

    async def test_unwired_backend_is_off(self) -> None:
        result = await _resolve_memory_health(self._app_state(backend=None))
        assert result.state is MemoryState.OFF


@pytest.mark.unit
class TestMemoryReadiness:
    """Only memory that cannot answer at all gates traffic.

    A wired durable backend that fails its probe (UNREACHABLE) fails
    ``/readyz`` (503), because its reads and writes are failing. Every
    other state serves correct results and must not: DEGRADED differs only
    in latency or in matching by term rather than meaning, and an unwired
    backend (OFF) is a not-yet-configured deployment rather than a runtime
    failure. The inmemory store is degraded by design and never blocks.
    """

    @staticmethod
    def _health(*, backend: str, state: MemoryState) -> MemoryHealth:
        return MemoryHealth(state=state, backend=backend)

    def test_inmemory_never_blocks(self) -> None:
        health = self._health(backend="inmemory", state=MemoryState.DEGRADED)
        assert _memory_readiness(health) is None

    def test_durable_backend_when_durable_is_ready(self) -> None:
        health = self._health(backend="sqlvector", state=MemoryState.DURABLE)
        assert _memory_readiness(health) is True

    def test_unreachable_backend_fails_readiness(self) -> None:
        health = self._health(backend="sqlvector", state=MemoryState.UNREACHABLE)
        assert _memory_readiness(health) is False

    def test_degraded_backend_does_not_block(self) -> None:
        # An unindexed dense column, or the built-in embedder, answers every
        # query correctly. Failing readiness for that takes a working system
        # offline over a latency or recall-quality cost the memory surface
        # already reports.
        health = self._health(backend="sqlvector", state=MemoryState.DEGRADED)
        assert _memory_readiness(health) is None

    def test_unwired_backend_does_not_block(self) -> None:
        # OFF is a minimal or not-yet-configured deployment (the config
        # default is sqlvector), not a runtime failure, so it must not
        # fail the readiness probe of every memory-less stack.
        health = self._health(backend="sqlvector", state=MemoryState.OFF)
        assert _memory_readiness(health) is None


@pytest.mark.unit
class TestProbePersistence:
    """``_probe_persistence`` distinguishes absent-by-design from failure."""

    def _app_state(self, *, backend: object, expected: bool) -> AppState:
        app_state = MagicMock(spec=AppState)
        app_state.slice.return_value = SimpleNamespace(
            backend=backend, persistence_expected=expected
        )
        return app_state

    async def test_expected_but_absent_is_unavailable(self) -> None:
        # A configured-but-absent backend is a real failure, not a
        # deliberately persistence-less dev run.
        result = await _probe_persistence(self._app_state(backend=None, expected=True))
        assert result is False

    async def test_unconfigured_absent_is_none(self) -> None:
        result = await _probe_persistence(self._app_state(backend=None, expected=False))
        assert result is None

    async def test_connected_backend_is_health_checked(self) -> None:
        backend = FakePersistenceBackend()
        await backend.connect()
        result = await _probe_persistence(
            self._app_state(backend=backend, expected=True)
        )
        assert result is True


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
