"""Tests for the unified ``POST /meta/chat/turn`` endpoint."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.intent_router import (
    IntentOutcome,
    IntentRoutingReason,
    TurnIntent,
)
from synthorg.meta.chief_of_staff.models import (
    ChatResponse,
    ConversationTurn,
    ProposeResult,
)
from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
)
from synthorg.meta.signals.service import SignalsService
from synthorg.meta.state import MetaStateSlice
from synthorg.settings.enums import SettingSource
from tests._shared import LoopAsyncClient, sid
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_BASE = "/api/v1/meta/chat/turn"
_HEADERS = make_auth_headers("ceo")


def _empty_snapshot() -> OrgSignalSnapshot:
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
        scaling=OrgScalingSummary(),
        errors=OrgErrorSummary(),
        evolution=OrgEvolutionSummary(),
        telemetry=OrgTelemetrySummary(),
    )


class _FixedClassifier:
    """An intent classifier that always returns a preset outcome."""

    def __init__(self, outcome: IntentOutcome) -> None:
        self._outcome = outcome

    async def classify(self, history: tuple[ConversationTurn, ...]) -> IntentOutcome:
        del history
        return self._outcome


class TestMetaTurn:
    async def test_503_when_router_disabled(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """Flipping turn_router_enabled off 503s before any dispatch."""
        from synthorg.settings.state import settings_service_of

        app_state = async_test_client.app.state.app_state
        settings = settings_service_of(app_state)
        prior = await settings.get("chief_of_staff", "turn_router_enabled")
        try:
            await settings.set("chief_of_staff", "turn_router_enabled", "false")
            resp = await async_test_client.post(
                _BASE, headers=_HEADERS, json={"message": "How are we doing?"}
            )
            assert resp.status_code == 503
        finally:
            if prior.source is SettingSource.DATABASE:
                await settings.set("chief_of_staff", "turn_router_enabled", prior.value)
            else:
                await settings.delete("chief_of_staff", "turn_router_enabled")

    async def test_no_classifier_dispatches_explain(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """With no classifier wired, a turn defaults to a plain answer."""
        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        chat_mock.ask.return_value = ChatResponse(
            answer="Quality is up.", sources=("performance",), confidence=0.8
        )
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.get_org_snapshot.return_value = _empty_snapshot()
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(
            MetaStateSlice,
            chief_of_staff_chat=chat_mock,
            signals_service=signals_mock,
            turn_intent_classifier=None,
        )
        try:
            resp = await async_test_client.post(
                _BASE, headers=_HEADERS, json={"message": "How are we doing?"}
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["intent"] == "explain"
            assert data["intent_reason"] == "no_intent_classifier"
            assert data["answer"]["answer"] == "Quality is up."
            chat_mock.ask.assert_awaited_once()
        finally:
            app_state.swap_slice(original)

    async def test_override_propose_dispatches_proposer(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """An explicit intent override skips classification and proposes."""
        proposer = AsyncMock(spec=ChiefOfStaffProposer)
        proposer.converse.return_value = ProposeResult(
            conversation_id=sid("conv-9"),
            status="needs_clarification",
            clarifying_question="Which platform?",
        )
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, chief_of_staff_proposer=proposer)
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"message": "build a landing page", "intent_override": "propose"},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["intent"] == "propose"
            assert data["intent_reason"] == "explicit_override"
            assert data["propose"]["clarifying_question"] == "Which platform?"
            proposer.converse.assert_awaited_once()
        finally:
            app_state.swap_slice(original)

    async def test_act_without_named_agent_degrades_to_explain(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A classified act naming no agent answers instead of acting."""
        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        chat_mock.ask.return_value = ChatResponse(
            answer="Here is what I'd do.", sources=(), confidence=0.6
        )
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.get_org_snapshot.return_value = _empty_snapshot()
        classifier = _FixedClassifier(
            IntentOutcome(
                intent=TurnIntent.ACT,
                reason=IntentRoutingReason.CLASSIFIED,
                confidence=0.95,
            )
        )
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(
            MetaStateSlice,
            chief_of_staff_chat=chat_mock,
            signals_service=signals_mock,
            turn_intent_classifier=classifier,
        )
        try:
            resp = await async_test_client.post(
                _BASE, headers=_HEADERS, json={"message": "update the thing now"}
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["intent"] == "explain"
            assert data["intent_reason"] == "act_no_target"
            chat_mock.ask.assert_awaited_once()
        finally:
            app_state.swap_slice(original)

    async def test_act_is_fail_closed_when_direct_mcp_off(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A named act 503s while direct_mcp_enabled is off (default)."""
        classifier = _FixedClassifier(
            IntentOutcome(
                intent=TurnIntent.ACT,
                reason=IntentRoutingReason.CLASSIFIED,
                confidence=0.95,
                named_targets=(NotBlankStr("CFO"),),
            )
        )
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, turn_intent_classifier=classifier)
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"message": "have the CFO send the invoice now"},
            )
            # direct_mcp_enabled is off by default: acting fails closed rather
            # than being answered as a read.
            assert resp.status_code == 503
        finally:
            app_state.swap_slice(original)
