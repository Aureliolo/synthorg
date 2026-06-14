"""Unit tests for the signals MCP handlers.

Exercises each of the 9 handlers against a fake SignalsService, covering
the windowed-read path (6 per-domain + snapshot), proposal listing, and
the destructive proposal submission path with all three guardrail
branches (missing actor / missing confirm / missing reason).
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.meta.mcp.handlers.signals import SIGNAL_HANDLERS
from synthorg.meta.signal_models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
)
from synthorg.meta.state import MetaStateSlice
from synthorg.observability.events.mcp import MCP_ADMIN_OP_EXECUTED
from tests._shared import as_uuid, make_app_state
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


def _empty_snapshot() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=0.0,
            avg_success_rate=0.0,
            avg_collaboration_score=0.0,
            agent_count=0,
        ),
        budget=OrgBudgetSummary(
            total_spend=0.0,
            productive_ratio=0.0,
            coordination_ratio=0.0,
            system_ratio=0.0,
            forecast_confidence=0.0,
            orchestration_overhead=0.0,
        ),
        coordination=OrgCoordinationSummary(),
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


@pytest.fixture
def fake_signals_service() -> AsyncMock:
    service = AsyncMock()
    service.get_org_snapshot = AsyncMock(return_value=_empty_snapshot())
    service.get_performance = AsyncMock(
        return_value=OrgPerformanceSummary(
            avg_quality_score=0.0,
            avg_success_rate=0.0,
            avg_collaboration_score=0.0,
            agent_count=0,
        ),
    )
    service.get_budget = AsyncMock(
        return_value=OrgBudgetSummary(
            total_spend=0.0,
            productive_ratio=0.0,
            coordination_ratio=0.0,
            system_ratio=0.0,
            forecast_confidence=0.0,
            orchestration_overhead=0.0,
        ),
    )
    service.get_coordination = AsyncMock(return_value=OrgCoordinationSummary())
    service.get_scaling_history = AsyncMock(return_value=OrgScalingSummary())
    service.get_error_patterns = AsyncMock(return_value=OrgErrorSummary())
    service.get_evolution_outcomes = AsyncMock(return_value=OrgEvolutionSummary())
    service.list_proposals = AsyncMock(return_value=((), 0))
    service.submit_proposal = AsyncMock(
        return_value=ApprovalItem(
            id=as_uuid("proposal-1"),
            action_type="signals.proposal",
            title="Test",
            description="desc",
            requested_by="actor-name",
            risk_level=ApprovalRiskLevel.LOW,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(UTC),
        ),
    )
    return service


@pytest.fixture
def fake_app_state(fake_signals_service: AsyncMock) -> AppState:
    return make_app_state(
        slices={MetaStateSlice: {"signals_service": fake_signals_service}},
    )


def _now_iso(offset_minutes: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(minutes=offset_minutes)).isoformat()


class TestSnapshotHandler:
    async def test_happy_path(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_get_org_snapshot"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"since": _now_iso(-60)},
        )
        payload = json.loads(response)
        assert payload["status"] == "ok"
        assert "performance" in payload["data"]

    async def test_missing_since_returns_invalid(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_get_org_snapshot"]
        response = await handler(
            app_state=fake_app_state,
            arguments={},
        )
        payload = json.loads(response)
        assert payload["status"] == "error"
        assert payload["domain_code"] == "invalid_argument"

    async def test_naive_datetime_rejected(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_get_org_snapshot"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"since": "2026-04-23T00:00:00"},  # no tz
        )
        assert json.loads(response)["status"] == "error"

    async def test_inverted_window_rejected(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_get_org_snapshot"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "since": _now_iso(),
                "until": _now_iso(-60),
            },
        )
        assert json.loads(response)["status"] == "error"


class TestPerDomainHandlers:
    @pytest.mark.parametrize(
        "tool",
        [
            "synthorg_signals_get_performance",
            "synthorg_signals_get_budget",
            "synthorg_signals_get_coordination",
            "synthorg_signals_get_scaling_history",
            "synthorg_signals_get_error_patterns",
            "synthorg_signals_get_evolution_outcomes",
        ],
    )
    async def test_happy_path(
        self,
        fake_app_state: AppState,
        tool: str,
    ) -> None:
        handler = SIGNAL_HANDLERS[tool]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "since": _now_iso(-60),
                "until": _now_iso(),
            },
        )
        payload = json.loads(response)
        assert payload["status"] == "ok"


class TestProposalsList:
    async def test_happy_path(
        self,
        fake_app_state: AppState,
        fake_signals_service: AsyncMock,
    ) -> None:
        fake_signals_service.list_proposals = AsyncMock(
            return_value=(
                (
                    ApprovalItem(
                        id=as_uuid("p-1"),
                        action_type="signals.proposal",
                        title="t",
                        description="d",
                        requested_by="r",
                        risk_level=ApprovalRiskLevel.LOW,
                        status=ApprovalStatus.PENDING,
                        created_at=datetime.now(UTC),
                    ),
                ),
                1,
            ),
        )
        handler = SIGNAL_HANDLERS["synthorg_signals_get_proposals"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"offset": 0, "limit": 10},
        )
        payload = json.loads(response)
        assert payload["status"] == "ok"

    async def test_status_filter_applied(
        self,
        fake_app_state: AppState,
        fake_signals_service: AsyncMock,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_get_proposals"]
        await handler(
            app_state=fake_app_state,
            arguments={"status": "pending"},
        )
        fake_signals_service.list_proposals.assert_awaited_once()
        call_kwargs = fake_signals_service.list_proposals.call_args.kwargs
        assert call_kwargs["status"] == ApprovalStatus.PENDING

    async def test_invalid_status_rejected(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_get_proposals"]
        response = await handler(
            app_state=fake_app_state,
            arguments={"status": "not-a-status"},
        )
        assert json.loads(response)["status"] == "error"


class TestSubmitProposalGuardrails:
    """Destructive-op guardrail triple enforcement."""

    def _minimal_proposal(self) -> dict[str, object]:
        from synthorg.meta.models import (
            ProposalAltitude,
            ProposalRationale,
            RollbackOperation,
            RollbackPlan,
        )

        return {
            "altitude": ProposalAltitude.CONFIG_TUNING.value,
            "title": "test",
            "description": "description",
            "rationale": ProposalRationale(
                signal_summary="noisy errors",
                pattern_detected="high error rate on agent X",
                expected_impact="fewer errors",
                confidence_reasoning="backed by 100 samples",
            ).model_dump(mode="json"),
            "rollback_plan": RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="revert_config",
                        target="a.b",
                        description="revert change",
                    ),
                ),
                validation_check="config equals original value",
            ).model_dump(mode="json"),
            "confidence": 0.8,
            "config_changes": [
                {
                    "path": "a.b",
                    "old_value": "1",
                    "new_value": "2",
                    "description": "tune value",
                },
            ],
        }

    async def test_missing_actor_rejected(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_submit_proposal"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "proposal": self._minimal_proposal(),
                "confirm": True,
                "reason": "valid reason",
            },
            actor=None,
        )
        payload = json.loads(response)
        assert payload["status"] == "error"
        assert payload["domain_code"] == "guardrail_violated"
        assert "actor" in payload["message"].lower()

    async def test_missing_confirm_rejected(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_submit_proposal"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "proposal": self._minimal_proposal(),
                "reason": "valid",
            },
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["domain_code"] == "guardrail_violated"
        assert "confirm" in payload["message"].lower()

    async def test_missing_reason_rejected(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_submit_proposal"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "proposal": self._minimal_proposal(),
                "confirm": True,
            },
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["domain_code"] == "guardrail_violated"
        assert "reason" in payload["message"].lower()

    async def test_happy_path_calls_facade(
        self,
        fake_app_state: AppState,
        fake_signals_service: AsyncMock,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_submit_proposal"]
        actor = make_test_actor()
        with structlog.testing.capture_logs() as events:
            response = await handler(
                app_state=fake_app_state,
                arguments={
                    "proposal": self._minimal_proposal(),
                    "confirm": True,
                    "reason": "ship this change",
                },
                actor=actor,
            )
        payload = json.loads(response)
        assert payload["status"] == "ok"
        fake_signals_service.submit_proposal.assert_awaited_once()
        call_kwargs = fake_signals_service.submit_proposal.call_args.kwargs
        assert call_kwargs["actor"] is actor
        assert call_kwargs["reason"] == "ship this change"
        assert call_kwargs["proposal"].title == "test"
        assert any(
            e.get("event") == MCP_ADMIN_OP_EXECUTED
            and e.get("tool_name") == "synthorg_signals_submit_proposal"
            for e in events
        )

    async def test_invalid_proposal_rejected(
        self,
        fake_app_state: AppState,
    ) -> None:
        handler = SIGNAL_HANDLERS["synthorg_signals_submit_proposal"]
        response = await handler(
            app_state=fake_app_state,
            arguments={
                "proposal": {"altitude": "config_tuning"},  # missing required
                "confirm": True,
                "reason": "x",
            },
            actor=make_test_actor(),
        )
        payload = json.loads(response)
        assert payload["status"] == "error"
        assert payload["domain_code"] == "invalid_argument"
