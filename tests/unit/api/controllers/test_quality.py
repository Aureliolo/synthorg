"""Tests for QualityController."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.auth.service import AuthService
from synthorg.core.auth.config import AuthConfig
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import QualityOverride
from synthorg.hr.performance.quality_override_store import (
    QualityOverrideStore,
)
from synthorg.hr.performance.tracker import PerformanceTracker
from tests._shared import LoopAsyncClient
from tests._shared import build_test_app as create_app
from tests.unit.api.conftest import _seed_test_users, make_auth_headers
from tests.unit.api.fakes import FakeMessageBus, FakePersistenceBackend

NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)

_TEST_JWT_SECRET = "test-secret-that-is-at-least-32-characters-long"


@pytest.fixture
def quality_override_store() -> QualityOverrideStore:
    return QualityOverrideStore()


@pytest.fixture
def perf_tracker(
    quality_override_store: QualityOverrideStore,
) -> PerformanceTracker:
    return PerformanceTracker(quality_override_store=quality_override_store)


@pytest.fixture
async def quality_client(
    quality_override_store: QualityOverrideStore,
    perf_tracker: PerformanceTracker,
) -> AsyncGenerator[LoopAsyncClient]:
    """Test client with quality_override_store wired in."""
    from synthorg.budget.tracker import CostTracker
    from synthorg.config.schema import RootConfig
    from synthorg.engine.task_engine import TaskEngine

    fake_persistence = FakePersistenceBackend()
    await fake_persistence.connect()
    fake_bus = FakeMessageBus()
    await fake_bus.start()

    auth_service = AuthService(AuthConfig(jwt_secret=_TEST_JWT_SECRET))
    _seed_test_users(fake_persistence, auth_service)

    app = create_app(
        config=RootConfig(company_name="test-company"),
        persistence=fake_persistence,
        message_bus=fake_bus,
        cost_tracker=CostTracker(),
        approval_store=ApprovalStore(),
        auth_service=auth_service,
        task_engine=TaskEngine(persistence=fake_persistence),
        performance_tracker=perf_tracker,
    )
    async with LoopAsyncClient(app) as client:
        client.headers.update(make_auth_headers("ceo"))
        yield client


@pytest.mark.unit
class TestGetOverride:
    """GET /agents/{agent_id}/quality/override."""

    async def test_404_when_no_override(
        self,
        quality_client: LoopAsyncClient,
    ) -> None:
        """No override -> 404."""
        resp = await quality_client.get(
            "/api/v1/agents/agent-001/quality/override",
        )
        assert resp.status_code == 404

    async def test_returns_active_override(
        self,
        quality_client: LoopAsyncClient,
        quality_override_store: QualityOverrideStore,
    ) -> None:
        """Active override -> 200 with override data."""
        quality_override_store.set_override(
            QualityOverride(
                agent_id=NotBlankStr("agent-001"),
                score=8.5,
                reason=NotBlankStr("Excellent output"),
                applied_by=NotBlankStr("manager"),
                applied_at=NOW,
            ),
        )
        resp = await quality_client.get(
            "/api/v1/agents/agent-001/quality/override",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["score"] == 8.5
        assert body["data"]["reason"] == "Excellent output"


@pytest.mark.unit
class TestSetOverride:
    """POST /agents/{agent_id}/quality/override."""

    async def test_sets_override(
        self,
        quality_client: LoopAsyncClient,
        quality_override_store: QualityOverrideStore,
    ) -> None:
        """POST sets an override and returns it."""
        resp = await quality_client.post(
            "/api/v1/agents/agent-001/quality/override",
            json={"score": 7.5, "reason": "Good work on the refactor"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["score"] == 7.5
        assert body["data"]["reason"] == "Good work on the refactor"

        # Verify stored.
        stored = quality_override_store.get_active_override(
            NotBlankStr("agent-001"),
        )
        assert stored is not None
        assert stored.score == 7.5

    async def test_sets_override_with_expiration(
        self,
        quality_client: LoopAsyncClient,
    ) -> None:
        """POST with expires_in_days sets expiration."""
        resp = await quality_client.post(
            "/api/v1/agents/agent-001/quality/override",
            json={
                "score": 6.0,
                "reason": "Temporary adjustment",
                "expires_in_days": 7,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        expires_at = body["data"]["expires_at"]
        assert expires_at is not None
        parsed = datetime.fromisoformat(expires_at)
        expected = datetime.now(UTC) + timedelta(days=7)
        assert abs((parsed - expected).total_seconds()) < 10

    async def test_observer_denied_write(
        self,
        quality_client: LoopAsyncClient,
    ) -> None:
        """Observer role cannot set overrides (write access denied)."""
        resp = await quality_client.post(
            "/api/v1/agents/agent-001/quality/override",
            json={"score": 5.0, "reason": "Test"},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestClearOverride:
    """DELETE /agents/{agent_id}/quality/override."""

    async def test_clears_override(
        self,
        quality_client: LoopAsyncClient,
        quality_override_store: QualityOverrideStore,
    ) -> None:
        """DELETE removes the active override and returns 204."""
        quality_override_store.set_override(
            QualityOverride(
                agent_id=NotBlankStr("agent-001"),
                score=8.0,
                reason=NotBlankStr("Temp"),
                applied_by=NotBlankStr("manager"),
                applied_at=NOW,
            ),
        )
        resp = await quality_client.delete(
            "/api/v1/agents/agent-001/quality/override",
        )
        assert resp.status_code == 204
        assert resp.content == b""

        # Verify removed.
        stored = quality_override_store.get_active_override(
            NotBlankStr("agent-001"),
        )
        assert stored is None

    async def test_404_when_nothing_to_clear(
        self,
        quality_client: LoopAsyncClient,
    ) -> None:
        """DELETE with no override -> 404."""
        resp = await quality_client.delete(
            "/api/v1/agents/agent-001/quality/override",
        )
        assert resp.status_code == 404


@pytest.mark.unit
class TestQualityRequestBodyValidation:
    """Request body validation for override endpoint."""

    @pytest.mark.parametrize(
        ("payload", "reason"),
        [
            ({"score": 11.0, "reason": "Test"}, "score above 10"),
            ({"score": -1.0, "reason": "Test"}, "negative score"),
            ({"score": 5.0, "reason": ""}, "blank reason"),
            (
                {"score": 5.0, "reason": "Test", "expires_in_days": 0},
                "zero expiration",
            ),
            (
                {"score": 5.0, "reason": "Test", "expires_in_days": 366},
                "expiration over 365",
            ),
        ],
    )
    async def test_invalid_payloads_rejected(
        self,
        quality_client: LoopAsyncClient,
        payload: dict[str, object],
        reason: str,
    ) -> None:
        """Invalid request bodies are rejected with 400."""
        resp = await quality_client.post(
            "/api/v1/agents/agent-001/quality/override",
            json=payload,
        )
        assert resp.status_code == 400, f"Expected 400 for: {reason}"


@pytest.mark.unit
class TestQualityPathParamValidation:
    """Path parameter validation."""

    async def test_oversized_agent_id_rejected(
        self,
        quality_client: LoopAsyncClient,
    ) -> None:
        long_id = "x" * 129
        resp = await quality_client.get(
            f"/api/v1/agents/{long_id}/quality/override",
        )
        assert resp.status_code == 400


@pytest.mark.unit
class TestQualityOverrideStoreNotConfigured:
    """Override endpoints return 503 when store is not configured."""

    @pytest.fixture
    async def no_store_client(
        self,
    ) -> AsyncGenerator[LoopAsyncClient]:
        """Test client with tracker but no quality override store."""
        from synthorg.budget.tracker import CostTracker
        from synthorg.config.schema import RootConfig

        fake_persistence = FakePersistenceBackend()
        await fake_persistence.connect()
        fake_bus = FakeMessageBus()
        await fake_bus.start()

        tracker = PerformanceTracker()  # No quality_override_store
        auth_service = AuthService(AuthConfig(jwt_secret=_TEST_JWT_SECRET))
        _seed_test_users(fake_persistence, auth_service)

        app = create_app(
            config=RootConfig(company_name="test-company"),
            persistence=fake_persistence,
            message_bus=fake_bus,
            cost_tracker=CostTracker(),
            approval_store=ApprovalStore(),
            auth_service=auth_service,
            performance_tracker=tracker,
        )
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("ceo"))
            yield client

    async def test_get_override_503(
        self,
        no_store_client: LoopAsyncClient,
    ) -> None:
        """GET override returns 503 when store not configured."""
        resp = await no_store_client.get(
            "/api/v1/agents/agent-001/quality/override",
        )
        assert resp.status_code == 503

    async def test_post_override_503(
        self,
        no_store_client: LoopAsyncClient,
    ) -> None:
        """POST override returns 503 when store not configured."""
        resp = await no_store_client.post(
            "/api/v1/agents/agent-001/quality/override",
            json={"score": 5.0, "reason": "Test"},
        )
        assert resp.status_code == 503

    async def test_delete_override_503(
        self,
        no_store_client: LoopAsyncClient,
    ) -> None:
        """DELETE override returns 503 when store not configured."""
        resp = await no_store_client.delete(
            "/api/v1/agents/agent-001/quality/override",
        )
        assert resp.status_code == 503
