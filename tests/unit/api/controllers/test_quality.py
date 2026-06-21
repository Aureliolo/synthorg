"""Tests for QualityController."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import QualityOverride
from synthorg.hr.performance.quality_override_store import (
    QualityOverrideStore,
)
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.state import HrStateSlice
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def quality_override_store() -> QualityOverrideStore:
    return QualityOverrideStore()


@pytest.fixture
def quality_client(
    async_test_client: LoopAsyncClient,
    quality_override_store: QualityOverrideStore,
) -> LoopAsyncClient:
    """Shared app client with a per-test quality-override-backed tracker.

    Wires a fresh ``PerformanceTracker`` onto ``HrStateSlice`` for the test;
    the conftest ``_restore_app_state`` step reverts the slice afterwards, so
    the session-scoped tracker is never mutated.
    """
    app_state = async_test_client.app.state.app_state
    app_state.wire(
        HrStateSlice,
        performance_tracker=PerformanceTracker(
            quality_override_store=quality_override_store
        ),
    )
    return async_test_client


@pytest.fixture
def no_store_client(
    async_test_client: LoopAsyncClient,
) -> LoopAsyncClient:
    """Shared app client whose tracker has no quality override store."""
    app_state = async_test_client.app.state.app_state
    app_state.wire(HrStateSlice, performance_tracker=PerformanceTracker())
    return async_test_client


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
