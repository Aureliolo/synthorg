"""Acceptance (#1970): one human, several agents, attributed round-robin.

Drives the REAL :class:`GroupChatService` over the REAL meeting agent
caller (``build_meeting_agent_caller``) backed by a single
``ScriptedDriver`` -- zero LLM spend, full path (provider -> caller ->
persona render -> service -> persistence). Three C-suite agents share
one ``test-provider``, so the driver replays its three scripted
contributions in enrolment order (CEO, then CFO, then CTO).

The acceptance bar is the shared, growing context: contribution N's
prompt must contain every contribution < N (and the human message),
correctly attributed, and the persisted turns must carry the agent
attribution. The SEC-1 fencing (``<task-data>`` history,
``<peer-contribution>`` peers) is asserted on the wire each agent saw,
captured by a recording strategy wrapping the sequenced replay.
"""

import pytest

from synthorg.communication.meeting.agent_caller import build_meeting_agent_caller
from synthorg.core.enums import ConversationRole
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    TAG_TASK_DATA,
    wrap_untrusted,
)
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.group_chat import GroupChatService
from synthorg.meta.chief_of_staff.group_models import GroupConverseArgs
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
from tests._shared import FakeClock
from tests._shared.scripted_provider import make_text_response
from tests.unit.meta.chief_of_staff.group_chat_fakes import FakeParticipantRepo
from tests.unit.meta.chief_of_staff.propose_fakes import (
    START,
    FakeConversationRepo,
    FakeTurnRepo,
    build_registry,
    make_identity,
)

pytestmark = pytest.mark.e2e

_CEO_SAYS = "Strategy: prioritise the enterprise segment next quarter."
_CFO_SAYS = "Finance: that segment needs a 15% larger sales budget."
_CTO_SAYS = "Engineering: the platform scales to enterprise load already."


class _RecordingSequencedStrategy:
    """Sequenced replay that records each call's user prompt.

    ``SequencedResponseStrategy`` discards the messages it is handed, so
    the e2e wraps it to capture the user prompt each agent saw -- that
    growing prompt is exactly the shared-context invariant under test.
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


class TestGroupChatE2E:
    async def test_three_agents_share_growing_context(self) -> None:
        ceo = make_identity(name="Dana", role="CEO")
        cfo = make_identity(name="Casey", role="CFO")
        cto = make_identity(name="Tomas", role="CTO")
        registry = await build_registry(ceo, cfo, cto)
        # One shared driver; FIFO replay matches the enrolment-order
        # round-robin (CEO -> CFO -> CTO).
        strategy = _RecordingSequencedStrategy(
            (
                make_text_response(_CEO_SAYS),
                make_text_response(_CFO_SAYS),
                make_text_response(_CTO_SAYS),
            )
        )
        provider = ScriptedDriver("test-provider", strategy=strategy)
        provider_registry = ProviderRegistry({"test-provider": provider})
        agent_caller = build_meeting_agent_caller(
            agent_registry=registry,
            provider_registry=provider_registry,
        )
        turn_repo = FakeTurnRepo()
        service = GroupChatService(
            agent_caller=agent_caller,
            agent_registry=registry,
            config=ChiefOfStaffConfig(group_chat_enabled=True),
            conversation_repo=FakeConversationRepo(),  # type: ignore[arg-type]
            turn_repo=turn_repo,  # type: ignore[arg-type]
            participant_repo=FakeParticipantRepo(),  # type: ignore[arg-type]
            clock=FakeClock(start=START),
        )

        message = "Should we move upmarket to enterprise customers?"
        result = await service.converse(
            GroupConverseArgs(
                message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, message)),
                created_by=NotBlankStr("user-1"),
                participants=(
                    NotBlankStr(str(ceo.id)),
                    NotBlankStr(str(cfo.id)),
                    NotBlankStr(str(cto.id)),
                ),
            )
        )

        # All three contributed once, in enrolment order, fully attributed.
        assert len(strategy.user_prompts) == 3
        assert result.truncated_reason is None
        assert result.participants_skipped == ()
        assert [c.agent_name for c in result.contributions] == [
            "Dana",
            "Casey",
            "Tomas",
        ]
        assert [c.participant_role for c in result.contributions] == [
            "CEO",
            "CFO",
            "CTO",
        ]
        assert [c.content for c in result.contributions] == [
            _CEO_SAYS,
            _CFO_SAYS,
            _CTO_SAYS,
        ]
        assert [c.sequence for c in result.contributions] == [1, 2, 3]

        # The shared, growing context: the user prompt each agent saw.
        ceo_prompt, cfo_prompt, cto_prompt = strategy.user_prompts

        # Every agent sees the human question (fenced as <task-data>).
        assert message in ceo_prompt
        assert message in cfo_prompt
        assert message in cto_prompt
        assert TAG_TASK_DATA in ceo_prompt
        assert TAG_PEER_CONTRIBUTION in ceo_prompt

        # Contribution N's prompt contains every prior contribution and
        # none of its own / later ones (the round-robin invariant).
        assert _CEO_SAYS not in ceo_prompt
        assert _CEO_SAYS in cfo_prompt
        assert _CFO_SAYS not in cfo_prompt
        assert _CEO_SAYS in cto_prompt
        assert _CFO_SAYS in cto_prompt
        # Peers are attributed by name + role in the fenced peer block.
        assert "Dana (CEO)" in cfo_prompt
        assert "Casey (CFO)" in cto_prompt

        # Persistence: one USER turn then three attributed AGENT turns.
        roles = [t.role for t in sorted(turn_repo.turns, key=lambda t: t.sequence)]
        assert roles == [
            ConversationRole.USER,
            ConversationRole.AGENT,
            ConversationRole.AGENT,
            ConversationRole.AGENT,
        ]
        agent_turns = [t for t in turn_repo.turns if t.role is ConversationRole.AGENT]
        assert all(t.author_agent_id is not None for t in agent_turns)
        assert {t.author_name for t in agent_turns} == {"Dana", "Casey", "Tomas"}
