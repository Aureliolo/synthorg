"""Tests for the per-(provider, model) serviceability endpoints.

The surface exists because the health endpoint answers a different question
and cannot be made to answer this one: it is per provider, over a day, and
counts a reachability ping as evidence.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import ProviderConfig, RootConfig
from synthorg.providers.enums import AuthType
from synthorg.providers.health import (
    ProviderHealthRecord,
    ProviderOutcomeClass,
    RecordSource,
)
from synthorg.providers.health_tracker import ProviderHealthTracker
from synthorg.settings.registry import get_registry
from synthorg.settings.service import SettingsService
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
)

pytestmark = pytest.mark.unit

_NOW = datetime.now(UTC)
_HEADERS = make_auth_headers("ceo")
_PROVIDER = "test-provider"


def _record(
    *,
    model: str,
    outcome: ProviderOutcomeClass = ProviderOutcomeClass.SUCCESS,
    provider_name: str = _PROVIDER,
    latency_ms: float = 100.0,
    source: RecordSource = RecordSource.REAL_CALL,
) -> ProviderHealthRecord:
    """Build one recent outcome record."""
    succeeded = outcome is ProviderOutcomeClass.SUCCESS
    return ProviderHealthRecord(
        provider_name=provider_name,
        model=model,
        timestamp=_NOW - timedelta(seconds=1),
        success=succeeded,
        outcome_class=outcome,
        response_time_ms=latency_ms,
        error_message=None if succeeded else "upstream refused",
        source=source,
    )


def _build_client(
    *,
    fake_persistence: FakePersistenceBackend,
    fake_message_bus: FakeMessageBus,
    tracker: ProviderHealthTracker,
) -> LoopAsyncClient:
    """Build a client whose app carries *tracker*."""
    from synthorg.api.auth.service import AuthService
    from tests._shared import build_test_app as create_app
    from tests.unit.api.conftest import (
        _make_test_auth_service,
        _seed_test_users,
    )

    config = RootConfig(
        company_name="test",
        providers={_PROVIDER: ProviderConfig(auth_type=AuthType.NONE)},
    )
    auth_service: AuthService = _make_test_auth_service()
    _seed_test_users(fake_persistence, auth_service)
    app = create_app(
        config=config,
        persistence=fake_persistence,
        message_bus=fake_message_bus,
        cost_tracker=CostTracker(),
        auth_service=auth_service,
        settings_service=SettingsService(
            repository=fake_persistence.settings, registry=get_registry()
        ),
        provider_health_tracker=tracker,
    )
    return LoopAsyncClient(app)


class TestServiceabilityEndpoints:
    async def test_a_failing_model_is_visible_beside_a_healthy_sibling(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # The operator-facing point of the surface: on one connection, one
        # model queueing while another answers. A provider-level number is
        # the average that hides it.
        tracker = ProviderHealthTracker()
        for _ in range(4):
            await tracker.record(_record(model="test-small-001"))
        for _ in range(4):
            await tracker.record(
                _record(
                    model="test-large-001",
                    outcome=ProviderOutcomeClass.OVERLOADED,
                )
            )

        async with _build_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            tracker=tracker,
        ) as client:
            resp = await client.get(
                f"/api/v1/providers/{_PROVIDER}/serviceability", headers=_HEADERS
            )

        assert resp.status_code == 200
        by_model = {row["model"]: row for row in resp.json()["data"]}
        assert by_model["test-small-001"]["verdict"] == "up"
        assert by_model["test-large-001"]["verdict"] == "down"
        assert by_model["test-large-001"]["outcome_counts"]["overloaded"] == 4

    async def test_the_latency_distribution_is_reported(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        tracker = ProviderHealthTracker()
        for ms in (1200.0, 2650.0, 311000.0):
            await tracker.record(_record(model="test-large-001", latency_ms=ms))

        async with _build_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            tracker=tracker,
        ) as client:
            resp = await client.get(
                f"/api/v1/providers/{_PROVIDER}/serviceability", headers=_HEADERS
            )

        latency = resp.json()["data"][0]["latency"]
        assert latency["max_ms"] == pytest.approx(311000.0)
        assert latency["p50_ms"] < latency["max_ms"]

    async def test_probe_traffic_never_appears(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        # A probe names no model, so it can neither prove nor disprove that
        # one serves work.
        tracker = ProviderHealthTracker()
        await tracker.record(
            ProviderHealthRecord(
                provider_name=_PROVIDER,
                timestamp=_NOW - timedelta(seconds=1),
                success=True,
                response_time_ms=5.0,
                source=RecordSource.PROBE,
            )
        )

        async with _build_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            tracker=tracker,
        ) as client:
            resp = await client.get(
                "/api/v1/providers/serviceability", headers=_HEADERS
            )

        assert resp.json()["data"] == []

    async def test_the_fleet_view_spans_providers(
        self,
        fake_persistence: FakePersistenceBackend,
        fake_message_bus: FakeMessageBus,
    ) -> None:
        tracker = ProviderHealthTracker()
        await tracker.record(_record(model="test-small-001"))
        await tracker.record(
            _record(model="test-small-001", provider_name="other-provider")
        )

        async with _build_client(
            fake_persistence=fake_persistence,
            fake_message_bus=fake_message_bus,
            tracker=tracker,
        ) as client:
            fleet = await client.get(
                "/api/v1/providers/serviceability", headers=_HEADERS
            )
            scoped = await client.get(
                f"/api/v1/providers/{_PROVIDER}/serviceability", headers=_HEADERS
            )

        assert {row["provider_name"] for row in fleet.json()["data"]} == {
            _PROVIDER,
            "other-provider",
        }
        assert {row["provider_name"] for row in scoped.json()["data"]} == {_PROVIDER}

    async def test_auth_required(self, async_test_client: LoopAsyncClient) -> None:
        resp = await async_test_client.get(
            "/api/v1/providers/serviceability",
            headers={"Authorization": "Bearer invalid"},
        )
        assert resp.status_code == 401

    async def test_an_unknown_provider_is_a_404_not_an_empty_list(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        # An empty list is a true statement about a provider that exists and
        # has served nothing. Returning it for a name that does not exist
        # tells an operator who mistyped one that everything is fine.
        resp = await async_test_client.get(
            "/api/v1/providers/nonexistent/serviceability"
        )
        assert resp.status_code == 404
        assert resp.json()["success"] is False
