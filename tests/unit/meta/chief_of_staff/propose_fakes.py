# module-kind: tests
"""Shared in-memory doubles + builder for the proposer test suites.

Used by ``test_propose.py`` (the clarify-and-propose loop) and
``test_propose_routing.py`` (concern routing in front of it) so the two
suites share one set of repository doubles and one proposer builder.
"""

from datetime import UTC, date, datetime
from uuid import uuid4

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
)
from synthorg.core.enums import (
    AgentStatus,
    ConversationalProposalStatus,
    ConversationStatus,
    SeniorityLevel,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationalProposal,
    ConversationTurn,
    ProposeArgs,  # noqa: F401 -- re-exported for the proposer test suites
)
from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
from synthorg.meta.chief_of_staff.routing import RoleRouter
from synthorg.persistence.conversation_protocol import (
    ConversationTurnFilterSpec,
)
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalFilterSpec,
)
from synthorg.providers.registry import ProviderRegistry
from tests._shared import FakeClock
from tests._shared.scripted_provider import ScriptedProvider

START = datetime(2026, 5, 19, 9, 0, 0, tzinfo=UTC)


def make_identity(  # noqa: PLR0913 -- test identity builder: many independent knobs
    *,
    name: str,
    role: str,
    department: str = "executive",
    provider: str = "test-provider",
    model_id: str = "test-model-001",
    status: AgentStatus = AgentStatus.ACTIVE,
) -> AgentIdentity:
    """Build a C-suite ``AgentIdentity`` for the proposer test suites.

    Returns:
        A registered-shaped identity with the given role and provider.
    """
    return AgentIdentity(
        id=uuid4(),
        name=NotBlankStr(name),
        role=NotBlankStr(role),
        department=NotBlankStr(department),
        level=SeniorityLevel.C_SUITE,
        personality=PersonalityConfig(
            traits=(NotBlankStr("analytical"),),
            communication_style=NotBlankStr("concise"),
        ),
        model=ModelConfig(
            provider=NotBlankStr(provider),
            model_id=NotBlankStr(model_id),
            temperature=0.7,
            max_tokens=4096,
        ),
        hiring_date=date(2026, 1, 1),
        status=status,
    )


async def build_registry(*identities: AgentIdentity) -> AgentRegistryService:
    """Build an ``AgentRegistryService`` pre-populated with *identities*.

    Returns:
        The registry holding every supplied identity.
    """
    registry = AgentRegistryService()
    for identity in identities:
        await registry.register(identity)
    return registry


class FakeConversationRepo:
    """In-memory ``ConversationRepository`` double."""

    def __init__(self) -> None:
        self.items: dict[str, Conversation] = {}

    async def save(self, entity: Conversation) -> None:
        self.items[entity.id] = entity

    async def get(self, entity_id: str) -> Conversation | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[Conversation, ...]:
        return tuple(self.items.values())[offset : offset + limit]

    async def transition_if(
        self,
        entity_id: str,
        from_state: ConversationStatus,
        to_state: ConversationStatus,
        **updates: object,
    ) -> bool:
        current = self.items.get(entity_id)
        if current is None or current.status is not from_state:
            return False
        self.items[entity_id] = current.model_copy(update={"status": to_state})
        return True


class FakeTurnRepo:
    """In-memory append-only ``ConversationTurnRepository`` double."""

    def __init__(self) -> None:
        self.turns: list[ConversationTurn] = []

    async def append(self, event: ConversationTurn) -> None:
        self.turns.append(event)

    async def query(
        self,
        filter_spec: ConversationTurnFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ConversationTurn, ...]:
        rows = [
            t
            for t in self.turns
            if filter_spec.conversation_id is None
            or t.conversation_id == filter_spec.conversation_id
        ]
        rows.sort(key=lambda t: t.sequence, reverse=True)
        return tuple(rows[offset : offset + limit])

    async def purge_before(self, threshold: datetime) -> int:
        before = len(self.turns)
        self.turns = [t for t in self.turns if t.created_at >= threshold]
        return before - len(self.turns)


class FakeProposalRepo:
    """In-memory ``ConversationalProposalRepository`` double."""

    def __init__(self) -> None:
        self.items: dict[str, ConversationalProposal] = {}

    async def save(self, entity: ConversationalProposal) -> None:
        self.items[entity.id] = entity

    async def get(self, entity_id: str) -> ConversationalProposal | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ConversationalProposal, ...]:
        return tuple(self.items.values())[offset : offset + limit]

    async def transition_if(
        self,
        entity_id: str,
        from_state: ConversationalProposalStatus,
        to_state: ConversationalProposalStatus,
        **updates: object,
    ) -> bool:
        current = self.items.get(entity_id)
        if current is None or current.status is not from_state:
            return False
        self.items[entity_id] = current.model_copy(update={"status": to_state})
        return True

    async def query(
        self,
        filter_spec: ConversationalProposalFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ConversationalProposal, ...]:
        rows = [
            p
            for p in self.items.values()
            if (
                filter_spec.approval_id is None
                or p.approval_id == filter_spec.approval_id
            )
            and (
                filter_spec.conversation_id is None
                or p.conversation_id == filter_spec.conversation_id
            )
        ]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: ConversationalProposalFilterSpec) -> int:
        return len(await self.query(filter_spec))


def build_proposer(
    *,
    provider: ScriptedProvider,
    config: ChiefOfStaffConfig | None = None,
    role_router: RoleRouter | None = None,
    provider_registry: ProviderRegistry | None = None,
) -> tuple[
    ChiefOfStaffProposer,
    FakeConversationRepo,
    FakeTurnRepo,
    FakeProposalRepo,
    ApprovalStore,
]:
    """Build a proposer over in-memory doubles for the test suites.

    Returns:
        The proposer and its conversation / turn / proposal repos and
        the approval store, so a test can inspect persisted state.
    """
    conv_repo = FakeConversationRepo()
    turn_repo = FakeTurnRepo()
    proposal_repo = FakeProposalRepo()
    approval_store = ApprovalStore()
    proposer = ChiefOfStaffProposer(
        provider=provider,
        config=config or ChiefOfStaffConfig(propose_enabled=True),
        conversation_repo=conv_repo,
        turn_repo=turn_repo,
        proposal_repo=proposal_repo,
        approval_store=approval_store,
        clock=FakeClock(start=START),
        role_router=role_router,
        provider_registry=provider_registry,
    )
    return proposer, conv_repo, turn_repo, proposal_repo, approval_store
