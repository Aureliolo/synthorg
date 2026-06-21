"""Tests for CollaborationController."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.collaboration_override_store import (
    CollaborationOverrideStore,
)
from synthorg.hr.performance.models import CollaborationOverride
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.state import HrStateSlice
from tests._shared import LoopAsyncClient
from tests.unit.api.conftest import make_auth_headers

NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def override_store() -> CollaborationOverrideStore:
    return CollaborationOverrideStore()


@pytest.fixture
def collab_client(
    async_test_client: LoopAsyncClient,
    override_store: CollaborationOverrideStore,
) -> LoopAsyncClient:
    """Shared app client with a per-test override-store-backed tracker wired in.

    Wires a fresh ``PerformanceTracker`` onto ``HrStateSlice`` for the test;
    the conftest ``_restore_app_state`` step reverts the slice afterwards, so
    the session-scoped tracker is never mutated.
    """
    app_state = async_test_client.app.state.app_state
    app_state.wire(
        HrStateSlice,
        performance_tracker=PerformanceTracker(override_store=override_store),
    )
    return async_test_client


@pytest.mark.unit
class TestGetScore:
    """GET /agents/{agent_id}/collaboration/score."""

    async def test_returns_neutral_score(
        self,
        collab_client: LoopAsyncClient,
    ) -> None:
        """No collaboration data -> neutral 5.0 score."""
        resp = await collab_client.get("/api/v1/agents/agent-001/collaboration/score")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["score"] == 5.0
        assert body["data"]["override_active"] is False

    async def test_returns_override_when_active(
        self,
        collab_client: LoopAsyncClient,
        override_store: CollaborationOverrideStore,
    ) -> None:
        """Active override is reflected in the score."""
        override_store.set_override(
            CollaborationOverride(
                agent_id=NotBlankStr("agent-001"),
                score=9.0,
                reason=NotBlankStr("Good work"),
                applied_by=NotBlankStr("manager"),
                applied_at=NOW,
            ),
        )
        resp = await collab_client.get("/api/v1/agents/agent-001/collaboration/score")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["score"] == 9.0
        assert body["data"]["override_active"] is True


@pytest.mark.unit
class TestGetOverride:
    """GET /agents/{agent_id}/collaboration/override."""

    async def test_404_when_no_override(
        self,
        collab_client: LoopAsyncClient,
    ) -> None:
        """No override -> 404."""
        resp = await collab_client.get(
            "/api/v1/agents/agent-001/collaboration/override",
        )
        assert resp.status_code == 404

    async def test_returns_active_override(
        self,
        collab_client: LoopAsyncClient,
        override_store: CollaborationOverrideStore,
    ) -> None:
        """Active override -> 200 with override data."""
        override_store.set_override(
            CollaborationOverride(
                agent_id=NotBlankStr("agent-001"),
                score=8.0,
                reason=NotBlankStr("Mentoring"),
                applied_by=NotBlankStr("manager"),
                applied_at=NOW,
            ),
        )
        resp = await collab_client.get(
            "/api/v1/agents/agent-001/collaboration/override",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["score"] == 8.0
        assert body["data"]["reason"] == "Mentoring"


@pytest.mark.unit
class TestSetOverride:
    """POST /agents/{agent_id}/collaboration/override."""

    async def test_sets_override(
        self,
        collab_client: LoopAsyncClient,
        override_store: CollaborationOverrideStore,
    ) -> None:
        """POST sets an override and returns it."""
        resp = await collab_client.post(
            "/api/v1/agents/agent-001/collaboration/override",
            json={"score": 7.5, "reason": "Grace period"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["score"] == 7.5
        assert body["data"]["reason"] == "Grace period"

        # Verify stored.
        stored = override_store.get_active_override(
            NotBlankStr("agent-001"),
        )
        assert stored is not None
        assert stored.score == 7.5

    async def test_sets_override_with_expiration(
        self,
        collab_client: LoopAsyncClient,
    ) -> None:
        """POST with expires_in_days sets expiration."""
        resp = await collab_client.post(
            "/api/v1/agents/agent-001/collaboration/override",
            json={
                "score": 6.0,
                "reason": "Temporary",
                "expires_in_days": 7,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["expires_at"] is not None

    async def test_observer_denied_write(
        self,
        collab_client: LoopAsyncClient,
    ) -> None:
        """Observer role cannot set overrides (write access denied)."""
        resp = await collab_client.post(
            "/api/v1/agents/agent-001/collaboration/override",
            json={"score": 5.0, "reason": "Test"},
            headers=make_auth_headers("observer"),
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestClearOverride:
    """DELETE /agents/{agent_id}/collaboration/override."""

    async def test_clears_override(
        self,
        collab_client: LoopAsyncClient,
        override_store: CollaborationOverrideStore,
    ) -> None:
        """DELETE removes the active override and returns 204 with empty body."""
        override_store.set_override(
            CollaborationOverride(
                agent_id=NotBlankStr("agent-001"),
                score=8.0,
                reason=NotBlankStr("Temp"),
                applied_by=NotBlankStr("manager"),
                applied_at=NOW,
            ),
        )
        resp = await collab_client.delete(
            "/api/v1/agents/agent-001/collaboration/override",
        )
        assert resp.status_code == 204
        assert resp.content == b""

        # Verify removed.
        stored = override_store.get_active_override(
            NotBlankStr("agent-001"),
        )
        assert stored is None

    async def test_404_when_nothing_to_clear(
        self,
        collab_client: LoopAsyncClient,
    ) -> None:
        """DELETE with no override -> 404."""
        resp = await collab_client.delete(
            "/api/v1/agents/agent-001/collaboration/override",
        )
        assert resp.status_code == 404


@pytest.mark.unit
class TestCollaborationPathParamValidation:
    """Path parameter validation via Litestar Parameter constraints."""

    async def test_oversized_agent_id_rejected(
        self,
        collab_client: LoopAsyncClient,
    ) -> None:
        long_id = "x" * 129
        resp = await collab_client.get(
            f"/api/v1/agents/{long_id}/collaboration/score",
        )
        assert resp.status_code == 400


@pytest.mark.unit
class TestOverrideStoreNotConfigured:
    """Override endpoints return 503 when store is not configured."""

    @pytest.fixture
    def no_store_client(
        self,
        async_test_client: LoopAsyncClient,
    ) -> LoopAsyncClient:
        """Shared app client whose tracker has no override store configured."""
        app_state = async_test_client.app.state.app_state
        app_state.wire(HrStateSlice, performance_tracker=PerformanceTracker())
        return async_test_client

    @pytest.mark.parametrize(
        ("method", "json_body"),
        [
            ("GET", None),
            ("POST", {"score": 5.0, "reason": "Test"}),
            ("DELETE", None),
        ],
        ids=["get", "post", "delete"],
    )
    async def test_override_returns_503(
        self,
        no_store_client: LoopAsyncClient,
        method: str,
        json_body: dict[str, object] | None,
    ) -> None:
        """Override endpoints return 503 when store is not configured."""
        resp = await no_store_client.request(
            method,
            "/api/v1/agents/agent-001/collaboration/override",
            json=json_body,
        )
        assert resp.status_code == 503


@pytest.mark.unit
class TestGetCalibration:
    """GET /agents/{agent_id}/collaboration/calibration."""

    async def test_returns_empty_when_no_sampler(
        self,
        collab_client: LoopAsyncClient,
    ) -> None:
        """No sampler configured -> empty calibration data."""
        resp = await collab_client.get(
            "/api/v1/agents/agent-001/collaboration/calibration",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["record_count"] == 0
        assert body["data"]["average_drift"] is None

    async def test_returns_calibration_when_sampler_configured(
        self,
        collab_client: LoopAsyncClient,
    ) -> None:
        """Sampler with records -> returns calibration data."""
        from unittest.mock import MagicMock

        from tests.unit.hr.performance.conftest import make_calibration_record

        mock_sampler = MagicMock()
        cal_rec = make_calibration_record(
            llm_score=8.0,
            behavioral_score=6.0,
        )
        mock_sampler.get_calibration_records.return_value = (cal_rec,)
        mock_sampler.get_drift_summary.return_value = 2.0
        # Patch the per-test wired tracker (reverted by _restore_app_state),
        # never the session-scoped one.
        app_state = collab_client.app.state.app_state
        app_state.slice(HrStateSlice).performance_tracker._sampler = mock_sampler

        resp = await collab_client.get(
            "/api/v1/agents/agent-001/collaboration/calibration",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["record_count"] == 1
        assert body["data"]["average_drift"] == 2.0
