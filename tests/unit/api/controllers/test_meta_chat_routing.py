"""Unit tests for ``resolve_chat_answer``, the /meta/chat routing helper.

Covers the fork the controller handler delegates to: ``alert_id`` set
and resolvable routes to ``explain_alert``; ``alert_id`` set but
unresolvable (or no alert repo wired) falls back to ``ask()``;
``proposal_id`` set and resolvable folds the approval-queue item into
``ask(..., scoped_proposal=...)``; ``proposal_id`` set but unresolvable
falls back to plain ``ask()``; neither set is plain ``ask()``; alert
takes priority when both are set.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._meta_chat_routing import resolve_chat_answer
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.models import Alert, ChatQuery, ChatResponse
from synthorg.meta.chief_of_staff.org_state import OrgStateSnapshot
from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    RuleSeverity,
)
from synthorg.meta.state import MetaStateSlice
from synthorg.observability.events.meta import (
    META_CHAT_DEPENDENCY_UNAVAILABLE,
    META_CHAT_SCOPE_NOT_FOUND,
)
from synthorg.persistence.alert_protocol import AlertRepository
from tests._shared import make_app_state, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
# Threaded verbatim through every free-form ``ask`` call; the routing helper
# forwards it unchanged (the alert-explain path stays scoped and ignores it).
_ORG_STATE = OrgStateSnapshot(read_at=_NOW)


def _snapshot() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=7.0,
            avg_success_rate=0.8,
            avg_collaboration_score=6.5,
            agent_count=5,
        ),
        budget=OrgBudgetSummary(
            total_spend=10.0,
            productive_ratio=0.6,
            coordination_ratio=0.3,
            system_ratio=0.1,
            forecast_confidence=0.8,
            orchestration_overhead=0.2,
        ),
        coordination=OrgCoordinationSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


def _alert(**overrides: object) -> Alert:
    base: dict[str, object] = {
        "severity": RuleSeverity.WARNING,
        "alert_type": "inflection",
        "description": NotBlankStr("Quality dropped"),
        "affected_domains": (NotBlankStr("performance"),),
        "emitted_at": _NOW,
    }
    base.update(overrides)
    return Alert(**base)  # type: ignore[arg-type]


def _proposal_item(**overrides: object) -> ApprovalItem:
    base: dict[str, object] = {
        "action_type": "signals.proposal",
        "title": "Tune retry backoff",
        "description": "Increase base delay to cut thrash",
        "requested_by": "meta_improvement_service",
        "risk_level": ApprovalRiskLevel.MEDIUM,
        "status": ApprovalStatus.PENDING,
        "created_at": _NOW,
        "metadata": {"altitude": "config_tuning", "source_rule": "retry_thrash"},
    }
    base.update(overrides)
    return ApprovalItem(**base)  # type: ignore[arg-type]


def _chat_backend() -> AsyncMock:
    mock = AsyncMock(spec=ChiefOfStaffChat)
    mock.ask.return_value = ChatResponse(
        answer="ask answer", sources=(), confidence=0.5
    )
    mock.explain_alert.return_value = ChatResponse(
        answer="alert answer", sources=(), confidence=0.9
    )
    return mock


class TestResolveChatAnswerNoScope:
    async def test_neither_id_set_calls_ask(self) -> None:
        state = make_app_state()
        chat_backend = _chat_backend()
        query = ChatQuery(question="How are we doing?")
        snapshot = _snapshot()

        result = await resolve_chat_answer(
            state, chat_backend, query, snapshot, _ORG_STATE
        )

        assert result.answer == "ask answer"
        chat_backend.ask.assert_awaited_once_with(query, snapshot, org_state=_ORG_STATE)
        chat_backend.explain_alert.assert_not_awaited()


class TestResolveChatAnswerAlertScope:
    async def test_resolvable_alert_id_routes_to_explain_alert(self) -> None:
        alert = _alert()
        repo = mock_of[AlertRepository](get_by_id=AsyncMock(return_value=alert))
        state = make_app_state()
        state.wire(MetaStateSlice, alert_repo=repo)
        chat_backend = _chat_backend()
        query = ChatQuery(question="Explain this alert", alert_id=alert.id)
        snapshot = _snapshot()

        result = await resolve_chat_answer(
            state, chat_backend, query, snapshot, _ORG_STATE
        )

        assert result.answer == "alert answer"
        chat_backend.explain_alert.assert_awaited_once_with(alert, snapshot)
        chat_backend.ask.assert_not_awaited()
        repo.get_by_id.assert_awaited_once_with(alert.id)

    async def test_unresolvable_alert_id_falls_back_to_ask(self) -> None:
        repo = mock_of[AlertRepository](get_by_id=AsyncMock(return_value=None))
        state = make_app_state()
        state.wire(MetaStateSlice, alert_repo=repo)
        chat_backend = _chat_backend()
        query = ChatQuery(question="Explain this alert", alert_id=uuid4())
        snapshot = _snapshot()

        with capture_logs() as caplog:
            result = await resolve_chat_answer(
                state, chat_backend, query, snapshot, _ORG_STATE
            )

        assert result.answer == "ask answer"
        chat_backend.ask.assert_awaited_once_with(query, snapshot, org_state=_ORG_STATE)
        chat_backend.explain_alert.assert_not_awaited()
        # A resolved-but-empty lookup is a stale/deleted id, not an unwired
        # dependency -- the two must not share one event.
        events = [r.get("event") for r in caplog]
        assert META_CHAT_SCOPE_NOT_FOUND in events
        assert META_CHAT_DEPENDENCY_UNAVAILABLE not in events

    async def test_alert_repo_read_failure_falls_back_to_ask(self) -> None:
        repo = mock_of[AlertRepository](
            get_by_id=AsyncMock(side_effect=QueryError("db down"))
        )
        state = make_app_state()
        state.wire(MetaStateSlice, alert_repo=repo)
        chat_backend = _chat_backend()
        query = ChatQuery(question="Explain this alert", alert_id=uuid4())
        snapshot = _snapshot()

        with capture_logs() as caplog:
            result = await resolve_chat_answer(
                state, chat_backend, query, snapshot, _ORG_STATE
            )

        assert result.answer == "ask answer"
        chat_backend.ask.assert_awaited_once_with(query, snapshot, org_state=_ORG_STATE)
        chat_backend.explain_alert.assert_not_awaited()
        events = [r.get("event") for r in caplog]
        assert META_CHAT_DEPENDENCY_UNAVAILABLE in events

    async def test_no_alert_repo_wired_falls_back_to_ask(self) -> None:
        state = make_app_state()
        chat_backend = _chat_backend()
        query = ChatQuery(question="Explain this alert", alert_id=uuid4())
        snapshot = _snapshot()

        with capture_logs() as caplog:
            result = await resolve_chat_answer(
                state, chat_backend, query, snapshot, _ORG_STATE
            )

        assert result.answer == "ask answer"
        chat_backend.ask.assert_awaited_once_with(query, snapshot, org_state=_ORG_STATE)
        # An unwired repo is a dependency-availability problem, not a
        # stale/deleted alert id -- must not be reported as the latter.
        events = [r.get("event") for r in caplog]
        assert META_CHAT_DEPENDENCY_UNAVAILABLE in events
        assert META_CHAT_SCOPE_NOT_FOUND not in events


class TestResolveChatAnswerProposalScope:
    async def test_resolvable_proposal_id_folds_into_ask(self) -> None:
        store = ApprovalStore()
        item = _proposal_item()
        await store.add(item)
        state = make_app_state(approval_store=store)
        chat_backend = _chat_backend()
        query = ChatQuery(question="Explain this proposal", proposal_id=item.id)
        snapshot = _snapshot()

        result = await resolve_chat_answer(
            state, chat_backend, query, snapshot, _ORG_STATE
        )

        assert result.answer == "ask answer"
        chat_backend.ask.assert_awaited_once_with(
            query, snapshot, scoped_proposal=item, org_state=_ORG_STATE
        )

    async def test_unresolvable_proposal_id_falls_back_to_plain_ask(self) -> None:
        store = ApprovalStore()
        state = make_app_state(approval_store=store)
        chat_backend = _chat_backend()
        query = ChatQuery(question="Explain this proposal", proposal_id=uuid4())
        snapshot = _snapshot()

        with capture_logs() as caplog:
            result = await resolve_chat_answer(
                state, chat_backend, query, snapshot, _ORG_STATE
            )

        assert result.answer == "ask answer"
        chat_backend.ask.assert_awaited_once_with(query, snapshot, org_state=_ORG_STATE)
        events = [r.get("event") for r in caplog]
        assert META_CHAT_SCOPE_NOT_FOUND in events
        assert META_CHAT_DEPENDENCY_UNAVAILABLE not in events

    async def test_approval_store_read_failure_falls_back_to_plain_ask(self) -> None:
        store = mock_of[ApprovalStoreProtocol](
            get=AsyncMock(side_effect=QueryError("db down"))
        )
        state = make_app_state(approval_store=store)
        chat_backend = _chat_backend()
        query = ChatQuery(question="Explain this proposal", proposal_id=uuid4())
        snapshot = _snapshot()

        with capture_logs() as caplog:
            result = await resolve_chat_answer(
                state, chat_backend, query, snapshot, _ORG_STATE
            )

        assert result.answer == "ask answer"
        chat_backend.ask.assert_awaited_once_with(query, snapshot, org_state=_ORG_STATE)
        events = [r.get("event") for r in caplog]
        assert META_CHAT_DEPENDENCY_UNAVAILABLE in events

    async def test_no_approval_store_falls_back_to_plain_ask(self) -> None:
        state = make_app_state()
        state.wire(ApprovalStateSlice, store=None)
        chat_backend = _chat_backend()
        query = ChatQuery(question="Explain this proposal", proposal_id=uuid4())
        snapshot = _snapshot()

        with capture_logs() as caplog:
            result = await resolve_chat_answer(
                state, chat_backend, query, snapshot, _ORG_STATE
            )

        assert result.answer == "ask answer"
        chat_backend.ask.assert_awaited_once_with(query, snapshot, org_state=_ORG_STATE)
        events = [r.get("event") for r in caplog]
        assert META_CHAT_DEPENDENCY_UNAVAILABLE in events
        assert META_CHAT_SCOPE_NOT_FOUND not in events


class TestResolveChatAnswerBothScopesSet:
    async def test_alert_takes_priority_over_proposal(self) -> None:
        alert = _alert()
        repo = mock_of[AlertRepository](get_by_id=AsyncMock(return_value=alert))
        store = ApprovalStore()
        item = _proposal_item()
        await store.add(item)
        state = make_app_state(approval_store=store)
        state.wire(MetaStateSlice, alert_repo=repo)
        chat_backend = _chat_backend()
        query = ChatQuery(
            question="Explain this",
            proposal_id=item.id,
            alert_id=alert.id,
        )
        snapshot = _snapshot()

        result = await resolve_chat_answer(
            state, chat_backend, query, snapshot, _ORG_STATE
        )

        assert result.answer == "alert answer"
        chat_backend.explain_alert.assert_awaited_once_with(alert, snapshot)
        chat_backend.ask.assert_not_awaited()
