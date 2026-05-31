"""Acceptance (#1971): agent-initiated invite, human consent, handover.

Drives the REAL :class:`GroupChatService` + :class:`GroupInviteCoordinator`
over the REAL meeting agent caller (``build_meeting_agent_caller``)
backed by a single ``ScriptedDriver`` -- zero LLM spend, full path
(provider -> caller -> persona render -> service -> coordinator ->
persistence). Consent is granted through the REAL
:func:`signal_resume_intent` dispatcher (Flow 0.5), driven over an
``AppState`` that wires ONLY the approval store + invite / participant
repos + registry: no group-chat service, no coordinator, no provider.
That proves the consent decision resolves even with the invite feature
effectively off -- the park/resume split's reason for being.

The acceptance bar:

- A structured invite envelope parks a ``CONVERSATIONAL_INVITE`` approval
  (the agent is NOT added yet) and the parsed message -- never the raw
  JSON -- is the persisted contribution.
- Approve -> the invited agent joins; on its genuine first turn its
  prompt carries the fenced (``<task-data>``) inviter+reason handover,
  prepended above the shared transcript.
- Reject -> membership is unchanged and the agent never contributes.
"""

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._approval_review_gate import signal_resume_intent
from synthorg.api.state import AppState
from synthorg.communication.meeting.agent_caller import build_meeting_agent_caller
from synthorg.core.enums import ApprovalSource
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.group_chat import GroupChatService
from synthorg.meta.chief_of_staff.group_invite import GroupInviteCoordinator
from synthorg.meta.chief_of_staff.group_models import GroupConverseArgs
from synthorg.meta.state import MetaStateSlice
from synthorg.providers.drivers.scripted import (
    ScriptedDriver,
    SequencedResponseStrategy,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    ToolDefinition,
)
from synthorg.providers.registry import ProviderRegistry
from tests._shared import FakeClock, make_app_state
from tests._shared.scripted_provider import make_text_response
from tests.unit.meta.chief_of_staff.group_chat_fakes import (
    FakeInviteRepo,
    FakeParticipantRepo,
)
from tests.unit.meta.chief_of_staff.propose_fakes import (
    START,
    FakeConversationRepo,
    FakeTurnRepo,
    build_registry,
    make_identity,
)

pytestmark = pytest.mark.e2e

_REASON = "deep-dive on the Q3 budget variance"
_INVITE_ENVELOPE = (
    '{"message": "We need finance to weigh in.", '
    f'"invite": {{"target": "CFO", "reason": "{_REASON}"}}}}'
)
_PLAIN_ENVELOPE = '{"message": "Noted, here is my view.", "invite": null}'
_CFO_ENVELOPE = '{"message": "Finance perspective on the variance.", "invite": null}'


class _RecordingSequencedStrategy:
    """Sequenced replay that records each call's user prompt.

    ``SequencedResponseStrategy`` discards the messages it is handed, so
    the e2e wraps it to capture the user prompt each agent saw -- the
    handover preamble under test rides on that prompt.
    """

    def __init__(self, responses: tuple[CompletionResponse, ...]) -> None:
        self._inner = SequencedResponseStrategy(responses)
        self.user_prompts: list[str] = []

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Record the user prompt, then return the next scripted response.

        Returns:
            The next ``CompletionResponse`` from the sequenced replay.
        """
        user = next(
            (m.content for m in reversed(messages) if m.role is MessageRole.USER),
            None,
        )
        self.user_prompts.append(user or "")
        return self._inner.next_response(messages, model, tools, config)


def _build_service(
    registry: AgentRegistryService,
    strategy: _RecordingSequencedStrategy,
) -> tuple[
    GroupChatService,
    FakeParticipantRepo,
    FakeInviteRepo,
    ApprovalStore,
]:
    """Wire the real invite-enabled group chat over a scripted provider.

    Returns:
        The service plus its participant repo, invite repo, and approval
        store (the latter two shared with the coordinator).
    """
    provider = ScriptedDriver("test-provider", strategy=strategy)
    agent_caller = build_meeting_agent_caller(
        agent_registry=registry,
        provider_registry=ProviderRegistry({"test-provider": provider}),
    )
    config = ChiefOfStaffConfig(group_chat_enabled=True, invite_enabled=True)
    clock = FakeClock(start=START)
    participant_repo = FakeParticipantRepo()
    invite_repo = FakeInviteRepo()
    approval_store = ApprovalStore()
    coordinator = GroupInviteCoordinator(
        invite_repo=invite_repo,  # type: ignore[arg-type]
        approval_store=approval_store,
        agent_registry=registry,
        participant_repo=participant_repo,  # type: ignore[arg-type]
        config=config,
        clock=clock,
    )
    service = GroupChatService(
        agent_caller=agent_caller,
        agent_registry=registry,
        config=config,
        conversation_repo=FakeConversationRepo(),  # type: ignore[arg-type]
        turn_repo=FakeTurnRepo(),  # type: ignore[arg-type]
        participant_repo=participant_repo,  # type: ignore[arg-type]
        clock=clock,
        invite_coordinator=coordinator,
    )
    return service, participant_repo, invite_repo, approval_store


def _consent_app_state(
    *,
    approval_store: ApprovalStore,
    invite_repo: FakeInviteRepo,
    participant_repo: FakeParticipantRepo,
    registry: AgentRegistryService,
) -> AppState:
    """Build an AppState for the consent decision with NO invite feature.

    Only the approval store, the invite / participant repos, and the
    registry are wired -- there is no group-chat service or coordinator,
    so a passing resume proves the consent path is ungated.

    Returns:
        The composed ``AppState``.
    """
    return make_app_state(
        approval_store=approval_store,
        agent_registry=registry,
        clock=FakeClock(start=START),
        slices={
            MetaStateSlice: {
                "conversation_invite_repo": invite_repo,
                "conversation_participant_repo": participant_repo,
            }
        },
    )


class TestAgentInviteE2E:
    async def test_consent_adds_agent_with_context_handover(self) -> None:
        ceo = make_identity(name="Dana", role="CEO")
        cfo = make_identity(name="Fiona", role="CFO")
        registry = await build_registry(ceo, cfo)
        # FIFO replay matches dispatch order: round 1 has only the CEO;
        # round 2 dispatches CEO then the newly-joined CFO.
        strategy = _RecordingSequencedStrategy(
            (
                make_text_response(_INVITE_ENVELOPE),
                make_text_response(_PLAIN_ENVELOPE),
                make_text_response(_CFO_ENVELOPE),
            )
        )
        service, participant_repo, invite_repo, approval_store = _build_service(
            registry, strategy
        )

        # Round 1: the CEO requests the CFO; the invite parks, the agent
        # is NOT yet a participant, and the parsed message is the turn.
        round_one = await service.converse(
            GroupConverseArgs(
                message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, "Kick-off")),
                created_by=NotBlankStr("user-1"),
                participants=(NotBlankStr(str(ceo.id)),),
            )
        )
        assert [c.content for c in round_one.contributions] == [
            "We need finance to weigh in."
        ]
        assert len(round_one.pending_invites) == 1
        approval_id = round_one.pending_invites[0].approval_id
        approval = await approval_store.get(approval_id)
        assert approval is not None
        assert approval.source is ApprovalSource.CONVERSATIONAL_INVITE
        conversation_id = round_one.conversation_id

        # Consent via the canonical dispatcher, feature effectively off.
        app_state = _consent_app_state(
            approval_store=approval_store,
            invite_repo=invite_repo,
            participant_repo=participant_repo,
            registry=registry,
        )
        await signal_resume_intent(
            app_state, approval_id, approved=True, decided_by="operator-1"
        )
        roster = list(participant_repo.items.values())
        assert any(p.agent_name == "Fiona" for p in roster)

        # Round 2: the CFO takes its genuine first turn; its prompt
        # carries the fenced inviter+reason handover, prepended above the
        # transcript. The established CEO never re-sees it.
        round_two = await service.converse(
            GroupConverseArgs(
                message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, "Continue")),
                created_by=NotBlankStr("user-1"),
                conversation_id=conversation_id,
            )
        )
        assert "Fiona" in [c.agent_name for c in round_two.contributions]
        ceo_round_two = strategy.user_prompts[1]
        cfo_round_two = strategy.user_prompts[2]
        assert _REASON in cfo_round_two
        assert "Dana" in cfo_round_two
        assert TAG_TASK_DATA in cfo_round_two
        assert cfo_round_two.index(_REASON) < cfo_round_two.index(
            "## Conversation so far"
        )
        assert _REASON not in ceo_round_two

    async def test_reject_leaves_membership_unchanged(self) -> None:
        ceo = make_identity(name="Dana", role="CEO")
        cfo = make_identity(name="Fiona", role="CFO")
        registry = await build_registry(ceo, cfo)
        strategy = _RecordingSequencedStrategy(
            (
                make_text_response(_INVITE_ENVELOPE),
                make_text_response(_PLAIN_ENVELOPE),
            )
        )
        service, participant_repo, invite_repo, approval_store = _build_service(
            registry, strategy
        )

        round_one = await service.converse(
            GroupConverseArgs(
                message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, "Kick-off")),
                created_by=NotBlankStr("user-1"),
                participants=(NotBlankStr(str(ceo.id)),),
            )
        )
        approval_id = round_one.pending_invites[0].approval_id
        conversation_id = round_one.conversation_id

        app_state = _consent_app_state(
            approval_store=approval_store,
            invite_repo=invite_repo,
            participant_repo=participant_repo,
            registry=registry,
        )
        await signal_resume_intent(
            app_state, approval_id, approved=False, decided_by="operator-1"
        )
        # Declined: the CFO never joined.
        assert all(p.agent_name != "Fiona" for p in participant_repo.items.values())

        # Round 2: only the CEO is present, so only the CEO contributes.
        round_two = await service.converse(
            GroupConverseArgs(
                message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, "Continue")),
                created_by=NotBlankStr("user-1"),
                conversation_id=conversation_id,
            )
        )
        assert [c.agent_name for c in round_two.contributions] == ["Dana"]
