# module-kind: tests
"""Shared in-memory doubles + builder for the group-chat test suite.

Reuses the conversation / turn repo doubles + identity helpers from
``propose_fakes`` and adds a participant-repo double and a scripted
:data:`AgentCaller` so ``test_group_chat.py`` can drive a deterministic
round-robin round and inspect every prompt and persisted turn.
"""

from datetime import datetime

from synthorg.communication.meeting.models import AgentResponse
from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.enums import ConversationParticipantStatus
from synthorg.meta.chief_of_staff.group_chat import GroupChatService
from synthorg.meta.chief_of_staff.group_models import ConversationParticipant
from synthorg.persistence.conversation_participant_protocol import (
    ConversationParticipantFilterSpec,
)
from tests._shared import FakeClock
from tests.unit.meta.chief_of_staff.propose_fakes import (
    START,
    FakeConversationRepo,
    FakeTurnRepo,
)


class FakeParticipantRepo:
    """In-memory ``ConversationParticipantRepository`` double."""

    def __init__(self) -> None:
        self.items: dict[str, ConversationParticipant] = {}

    async def save(self, entity: ConversationParticipant) -> None:
        self.items[entity.id] = entity

    async def get(self, entity_id: str) -> ConversationParticipant | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def transition_if(
        self,
        entity_id: str,
        from_state: ConversationParticipantStatus,
        to_state: ConversationParticipantStatus,
        **updates: object,
    ) -> bool:
        if updates:
            msg = "participant transition_if accepts no update keys"
            raise ValueError(msg)
        current = self.items.get(entity_id)
        if current is None or current.status is not from_state:
            return False
        self.items[entity_id] = current.model_copy(update={"status": to_state})
        return True

    def _matches(
        self,
        participant: ConversationParticipant,
        filter_spec: ConversationParticipantFilterSpec,
    ) -> bool:
        if (
            filter_spec.conversation_id is not None
            and participant.conversation_id != filter_spec.conversation_id
        ):
            return False
        return not (
            filter_spec.status is not None
            and participant.status is not filter_spec.status
        )

    async def query(
        self,
        filter_spec: ConversationParticipantFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ConversationParticipant, ...]:
        rows = [p for p in self.items.values() if self._matches(p, filter_spec)]
        rows.sort(key=lambda p: (p.added_at, p.id))
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: ConversationParticipantFilterSpec) -> int:
        return len(await self.query(filter_spec))


class ScriptedAgentCaller:
    """Deterministic :data:`AgentCaller` double for group-chat tests.

    Returns a per-agent scripted contribution and records every call so a
    test can assert turn order, prompt content, and the per-call token
    budget. Agents in ``raise_for`` raise to exercise the abort path.
    """

    def __init__(
        self,
        responses: dict[str, str],
        *,
        input_tokens: int = 10,
        output_tokens: int = 20,
        raise_for: frozenset[str] = frozenset(),
    ) -> None:
        self._responses = responses
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._raise_for = raise_for
        self.calls: list[tuple[str, str, int, str]] = []

    async def __call__(
        self, agent_id: str, prompt: str, max_tokens: int, meeting_id: str
    ) -> AgentResponse:
        self.calls.append((agent_id, prompt, max_tokens, meeting_id))
        if agent_id in self._raise_for:
            msg = f"scripted failure for {agent_id}"
            raise RuntimeError(msg)
        return AgentResponse(
            agent_id=NotBlankStr(agent_id),
            content=self._responses.get(agent_id, f"contribution from {agent_id}"),
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            cost=0.0,
        )


def build_group_chat_service(
    *,
    agent_caller: ScriptedAgentCaller,
    registry: AgentRegistryService,
    config: ChiefOfStaffConfig | None = None,
    clock_start: datetime = START,
) -> tuple[
    GroupChatService,
    FakeConversationRepo,
    FakeTurnRepo,
    FakeParticipantRepo,
]:
    """Build a group-chat service over in-memory doubles.

    Returns:
        The service and its conversation / turn / participant repos so a
        test can inspect persisted state.
    """
    conv_repo = FakeConversationRepo()
    turn_repo = FakeTurnRepo()
    participant_repo = FakeParticipantRepo()
    service = GroupChatService(
        agent_caller=agent_caller,  # type: ignore[arg-type]
        agent_registry=registry,
        config=config or ChiefOfStaffConfig(group_chat_enabled=True),
        conversation_repo=conv_repo,  # type: ignore[arg-type]
        turn_repo=turn_repo,  # type: ignore[arg-type]
        participant_repo=participant_repo,  # type: ignore[arg-type]
        clock=FakeClock(start=clock_start),
    )
    return service, conv_repo, turn_repo, participant_repo
