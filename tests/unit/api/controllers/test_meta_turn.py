"""Tests for the unified ``POST /meta/chat/turn`` endpoint."""

from datetime import date
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus
from synthorg.hr.state import agent_registry_of
from synthorg.meta.chief_of_staff._multi_voice import ChimeIn
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.group_chat import GroupChatService
from synthorg.meta.chief_of_staff.group_models import GroupConverseResult
from synthorg.meta.chief_of_staff.intent_models import (
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
    OrgSignalSnapshot,
    OrgTelemetrySummary,
)
from synthorg.meta.signals.service import SignalsService
from synthorg.meta.state import MetaStateSlice
from synthorg.settings.enums import SettingSource
from tests._shared import LoopAsyncClient, as_uuid, sid
from tests.unit.api.conftest import make_auth_headers

pytestmark = pytest.mark.unit

_BASE = "/api/v1/meta/chat/turn"
_HEADERS = make_auth_headers("ceo")
#: A classified outcome names the model that produced it, so a fixed
#: classifier has to name one too.
_CLASSIFIER_MODEL = "example-capable-001"


def _empty_snapshot() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=7.0,
            avg_success_rate=0.8,
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


class _FixedClassifier:
    """An intent classifier that always returns a preset outcome."""

    def __init__(self, outcome: IntentOutcome) -> None:
        self._outcome = outcome

    async def classify(self, history: tuple[ConversationTurn, ...]) -> IntentOutcome:
        del history
        return self._outcome


class _FixedMultiVoiceRouter:
    """A multi-voice router that always returns preset chime-ins."""

    def __init__(self, chimes: tuple[ChimeIn, ...]) -> None:
        self._chimes = chimes

    async def chime(
        self,
        *,
        question: str,
        answer: str,
        active: tuple[AgentIdentity, ...],
    ) -> tuple[ChimeIn, ...]:
        del question, answer, active
        return self._chimes


def _active_agent() -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid("agent-cfo"),
        name=NotBlankStr("Casey"),
        role=NotBlankStr("CFO"),
        department=NotBlankStr("executive"),
        model=ModelConfig(
            provider=NotBlankStr("test-provider"),
            model_id=NotBlankStr("test-model-001"),
            temperature=0.7,
            max_tokens=4096,
        ),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


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

    async def test_explain_carries_specialist_chime_ins(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """An explain answer surfaces the wired router's attributed chime-ins."""
        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        chat_mock.ask.return_value = ChatResponse(
            answer="Runway is about 14 months.", sources=(), confidence=0.8
        )
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.get_org_snapshot.return_value = _empty_snapshot()
        router = _FixedMultiVoiceRouter(
            (ChimeIn(role="CFO", name="Casey", content="Watch the Q3 renewal."),)
        )
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        # The chime gate needs at least one active agent to attribute against.
        await agent_registry_of(app_state).register(_active_agent())
        app_state.wire(
            MetaStateSlice,
            chief_of_staff_chat=chat_mock,
            signals_service=signals_mock,
            turn_intent_classifier=None,
            multi_voice_router=router,
        )
        try:
            resp = await async_test_client.post(
                _BASE, headers=_HEADERS, json={"message": "How is our runway?"}
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["intent"] == "explain"
            assert data["chime_ins"] == [
                {"role": "CFO", "name": "Casey", "content": "Watch the Q3 renewal."}
            ]
        finally:
            app_state.swap_slice(original)

    async def test_multi_voice_opt_out_suppresses_chime_ins(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """With multi_voice_enabled off, a wired router adds no chime-ins."""
        from synthorg.settings.state import settings_service_of

        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        chat_mock.ask.return_value = ChatResponse(
            answer="Runway is fine.", sources=(), confidence=0.8
        )
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.get_org_snapshot.return_value = _empty_snapshot()
        router = _FixedMultiVoiceRouter(
            (ChimeIn(role="CFO", name="Casey", content="An aside."),)
        )
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        settings = settings_service_of(app_state)
        prior = await settings.get("chief_of_staff", "multi_voice_enabled")
        await agent_registry_of(app_state).register(_active_agent())
        app_state.wire(
            MetaStateSlice,
            chief_of_staff_chat=chat_mock,
            signals_service=signals_mock,
            turn_intent_classifier=None,
            multi_voice_router=router,
        )
        try:
            await settings.set("chief_of_staff", "multi_voice_enabled", "false")
            resp = await async_test_client.post(
                _BASE, headers=_HEADERS, json={"message": "How is our runway?"}
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["chime_ins"] == []
        finally:
            app_state.swap_slice(original)
            if prior.source is SettingSource.DATABASE:
                await settings.set("chief_of_staff", "multi_voice_enabled", prior.value)
            else:
                await settings.delete("chief_of_staff", "multi_voice_enabled")

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
                model=NotBlankStr(_CLASSIFIER_MODEL),
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
                model=NotBlankStr(_CLASSIFIER_MODEL),
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

    async def test_configure_is_fail_closed_when_console_off(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A configure turn 503s while operator_console_enabled is off."""
        classifier = _FixedClassifier(
            IntentOutcome(
                intent=TurnIntent.CONFIGURE,
                reason=IntentRoutingReason.CLASSIFIED,
                confidence=0.95,
                model=NotBlankStr(_CLASSIFIER_MODEL),
            )
        )
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, turn_intent_classifier=classifier)
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={"message": "connect our GitHub organisation"},
            )
            # operator_console_enabled is off by default: configuring fails
            # closed rather than being answered as a read.
            assert resp.status_code == 503
        finally:
            app_state.swap_slice(original)

    async def test_configure_from_read_only_actor_blocked(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A configure turn is side-effecting, so a read-only actor is denied.

        The mutation check fires on the final (CONFIGURE) intent before the
        console gate, so an unauthorised actor never reaches the console.
        """
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=make_auth_headers("observer"),
                json={
                    "message": "connect our issue tracker",
                    "intent_override": "configure",
                },
            )
            assert resp.status_code == 403
        finally:
            app_state.swap_slice(original)

    async def test_read_only_actor_may_explain(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """An actor without org-mutation rights can still ask a question.

        Mutation permission is enforced on the FINAL intent, so a read-only
        actor is never blanket-blocked from the read-only EXPLAIN path.
        """
        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        chat_mock.ask.return_value = ChatResponse(
            answer="Runway is fine.", sources=(), confidence=0.8
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
            # ``observer`` is seeded without any org role, so it cannot mutate.
            resp = await async_test_client.post(
                _BASE,
                headers=make_auth_headers("observer"),
                json={"message": "How are we doing?"},
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["intent"] == "explain"
        finally:
            app_state.swap_slice(original)

    async def test_read_only_actor_blocked_from_side_effecting(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A side-effecting intent from a read-only actor is denied.

        The mutation check fires once the intent is known to be side-effecting,
        before the proposer runs, so an unauthorised propose never mutates.
        """
        proposer = AsyncMock(spec=ChiefOfStaffProposer)
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, chief_of_staff_proposer=proposer)
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=make_auth_headers("observer"),
                json={"message": "build a landing page", "intent_override": "propose"},
            )
            assert resp.status_code == 403
            # The proposer must never be reached once permission is denied.
            proposer.converse.assert_not_awaited()
        finally:
            app_state.swap_slice(original)

    async def test_override_group_carries_named_targets(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """A deferred stream's named_targets reach the group roster on re-issue.

        Only honoured with an override (the buffered re-issue after a stream
        classifies a group), the request's ``named_targets`` become the group's
        participants instead of degrading to EXPLAIN for lack of a roster.
        """
        service = AsyncMock(spec=GroupChatService)
        service.converse.return_value = GroupConverseResult(
            conversation_id=sid("conv-g")
        )
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(MetaStateSlice, group_chat_service=service)
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={
                    "message": "have the CFO and CTO discuss the budget",
                    "intent_override": "group_convene",
                    "named_targets": ["CFO", "CTO"],
                },
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["intent"] == "group_convene"
            args = service.converse.await_args.args[0]
            assert args.participants == (NotBlankStr("CFO"), NotBlankStr("CTO"))
        finally:
            app_state.swap_slice(original)

    async def test_override_group_with_duplicate_targets_degrades(
        self, async_test_client: LoopAsyncClient
    ) -> None:
        """An override roster of one distinct voice never opens a group.

        The override path bypasses the classifier, so the dispatch gate must
        re-apply the two-distinct-participant rule: ("CFO", "cfo") is one voice
        and degrades to EXPLAIN rather than convening a one-member "group".
        """
        chat_mock = AsyncMock(spec=ChiefOfStaffChat)
        chat_mock.ask.return_value = ChatResponse(
            answer="Here is the budget picture.", sources=(), confidence=0.8
        )
        signals_mock = AsyncMock(spec=SignalsService)
        signals_mock.get_org_snapshot.return_value = _empty_snapshot()
        service = AsyncMock(spec=GroupChatService)
        app_state = async_test_client.app.state.app_state
        original = app_state.slice(MetaStateSlice)
        app_state.wire(
            MetaStateSlice,
            chief_of_staff_chat=chat_mock,
            signals_service=signals_mock,
            group_chat_service=service,
        )
        try:
            resp = await async_test_client.post(
                _BASE,
                headers=_HEADERS,
                json={
                    "message": "have the CFO and the cfo hash it out",
                    "intent_override": "group_convene",
                    "named_targets": ["CFO", "cfo"],
                },
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["intent"] == "explain"
            assert data["intent_reason"] == "group_targets_missing"
            # The group service must never run on a one-voice roster.
            service.converse.assert_not_awaited()
        finally:
            app_state.swap_slice(original)
