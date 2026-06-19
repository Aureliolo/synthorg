"""Tests for provider health endpoint."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import ProviderConfig, ProviderModelConfig, RootConfig
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderHealthTracker,
)
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)

_NOW = datetime.now(UTC)
_HEADERS = make_auth_headers("ceo")


def _make_health_record(
    *,
    provider_name: str = "test-provider",
    timestamp: datetime | None = None,
    success: bool = True,
    response_time_ms: float = 100.0,
    error_message: str | None = None,
) -> ProviderHealthRecord:
    return ProviderHealthRecord(
        provider_name=provider_name,
        timestamp=timestamp or _NOW,
        success=success,
        response_time_ms=response_time_ms,
        error_message=error_message,
    )


def _build_provider_client(
    *,
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    provider_health_tracker: ProviderHealthTracker | None = None,
    cost_tracker: CostTracker | None = None,
) -> LoopAsyncClient:
    """Build a LoopAsyncClient with a provider configured."""
    from synthorg.api.auth.service import AuthService
    from tests._shared import build_test_app as create_app
    from tests.unit.api.conftest import _make_test_auth_service, _seed_test_users

    config = RootConfig(
        company_name="test",
        providers={
            "test-provider": ProviderConfig(
                connection_name="conn-test",
                driver="litellm",
                models=(
                    ProviderModelConfig(
                        id="test-small-001",
                        alias="small",
                    ),
                ),
            ),
        },
    )
    auth_service: AuthService = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    settings_service = SettingsService(
        repository=fake_persistence.settings,
        registry=get_registry(),
    )
    app = create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=cost_tracker or CostTracker(),
        auth_service=auth_service,
        settings_service=settings_service,
        provider_health_tracker=provider_health_tracker or ProviderHealthTracker(),
    )
    return LoopAsyncClient(app)


@pytest.mark.unit
class TestProviderHealth:
    async def test_provider_not_found(
        self,
        async_test_client: LoopAsyncClient,
    ) -> None:
        resp = await async_test_client.get("/api/v1/providers/nonexistent/health")
        assert resp.status_code == 404
        assert resp.json()["success"] is False

    async def test_auth_required(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/providers/test-provider/health",
            headers={"Authorization": "Bearer invalid"},
        )
        assert resp.status_code == 401

    async def test_empty_health(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Provider exists but no health records."""
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["health_status"] == "unknown"
            assert data["last_check_timestamp"] is None
            assert data["avg_response_time_ms"] is None
            assert data["error_rate_percent_24h"] == 0.0
            assert data["calls_last_24h"] == 0

    async def test_healthy_provider(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Provider with all successful calls."""
        tracker = ProviderHealthTracker()
        for i in range(5):
            await tracker.record(
                _make_health_record(
                    timestamp=_NOW - timedelta(minutes=i),
                    response_time_ms=100.0 + i * 10,
                ),
            )
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            provider_health_tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["health_status"] == "up"
            assert data["calls_last_24h"] == 5
            assert data["error_rate_percent_24h"] == 0.0
            assert data["avg_response_time_ms"] is not None

    async def test_degraded_provider(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Provider with 20% error rate -> degraded."""
        tracker = ProviderHealthTracker()
        for i in range(10):
            ok = i >= 2  # 2 failures out of 10
            await tracker.record(
                _make_health_record(
                    timestamp=_NOW - timedelta(minutes=i),
                    success=ok,
                    error_message=None if ok else "test error",
                ),
            )
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            provider_health_tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["health_status"] == "degraded"
            assert data["error_rate_percent_24h"] == 20.0

    async def test_down_provider(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Provider with 100% error rate -> down."""
        tracker = ProviderHealthTracker()
        for i in range(3):
            await tracker.record(
                _make_health_record(
                    timestamp=_NOW - timedelta(minutes=i),
                    success=False,
                    error_message="test error",
                ),
            )
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            provider_health_tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["health_status"] == "down"
            assert data["error_rate_percent_24h"] == 100.0


@pytest.mark.unit
class TestProviderHealthUsageEnrichment:
    """Tests for cost/token enrichment of the health endpoint."""

    async def test_health_includes_zero_usage_when_no_cost_records(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Usage fields present and zero when no cost records exist."""
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total_tokens_24h"] == 0
            assert data["total_cost_24h"] == 0.0

    async def test_health_includes_usage_from_cost_tracker(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Usage fields reflect token/cost totals from CostTracker."""
        tracker = CostTracker()
        await tracker.record(
            CostRecord(
                agent_id="alice",
                task_id="task-1",
                provider="test-provider",
                model="test-small-001",
                input_tokens=3000,
                output_tokens=1000,
                cost=0.25,
                currency="EUR",
                timestamp=_NOW - timedelta(minutes=5),
            ),
        )
        await tracker.record(
            CostRecord(
                agent_id="bob",
                task_id="task-2",
                provider="test-provider",
                model="test-small-001",
                input_tokens=2000,
                output_tokens=500,
                cost=0.15,
                currency="EUR",
                timestamp=_NOW - timedelta(minutes=10),
            ),
        )
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            cost_tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total_tokens_24h"] == 6500  # 3000+1000+2000+500
            assert data["total_cost_24h"] == pytest.approx(0.40, abs=1e-9)

    async def test_health_excludes_other_provider_costs(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Costs from other providers are not included."""
        tracker = CostTracker()
        await tracker.record(
            CostRecord(
                agent_id="alice",
                task_id="task-1",
                provider="test-provider",
                model="test-small-001",
                input_tokens=1000,
                output_tokens=500,
                cost=0.10,
                currency="EUR",
                timestamp=_NOW - timedelta(minutes=5),
            ),
        )
        await tracker.record(
            CostRecord(
                agent_id="alice",
                task_id="task-2",
                provider="other-provider",
                model="other-model",
                input_tokens=9000,
                output_tokens=9000,
                cost=9.99,
                currency="EUR",
                timestamp=_NOW - timedelta(minutes=5),
            ),
        )
        async with _build_provider_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            cost_tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/test-provider/health",
                headers=_HEADERS,
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total_tokens_24h"] == 1500
            assert data["total_cost_24h"] == 0.10

    async def test_health_graceful_degradation_on_cost_tracker_error(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        """Endpoint returns 200 with zero usage when CostTracker raises."""
        from unittest.mock import AsyncMock, patch

        tracker = CostTracker()
        with patch.object(
            tracker,
            "get_provider_usage",
            new=AsyncMock(
                spec=tracker.get_provider_usage,
                side_effect=RuntimeError("cost tracker broken"),
            ),
        ):
            async with _build_provider_client(
                fake_persistence=fake_persistence,
                fake_message_bus=fake_message_bus,
                cost_tracker=tracker,
            ) as client:
                resp = await client.get(
                    "/api/v1/providers/test-provider/health",
                    headers=_HEADERS,
                )
                assert resp.status_code == 200
                data = resp.json()["data"]
                assert data["total_tokens_24h"] == 0
                assert data["total_cost_24h"] == 0.0
