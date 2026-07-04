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
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import (
    ProposeArgs,
)
from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
from synthorg.meta.chief_of_staff.routing import RoleRouter
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock
from tests._shared.conversation_fakes import (
    FakeConversationRepo,
    FakeProposalRepo,
    FakeTurnRepo,
)
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
    level: SeniorityLevel = SeniorityLevel.C_SUITE,
) -> AgentIdentity:
    """Build an ``AgentIdentity`` for the proposer test suites.

    The ``level`` parameter sets the seniority (defaulting to
    ``SeniorityLevel.C_SUITE``) so callers can build non-C-suite
    identities for the concern-routing tests.

    Returns:
        A registered-shaped identity with the given role and provider.
    """
    return AgentIdentity(
        id=uuid4(),
        name=NotBlankStr(name),
        role=NotBlankStr(role),
        department=NotBlankStr(department),
        level=level,
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


def build_proposer(
    *,
    provider: ScriptedProvider,
    config: ChiefOfStaffConfig | None = None,
    role_router: RoleRouter | None = None,
    provider_registry: ProviderRegistry | None = None,
    config_resolver: ConfigResolver | None = None,
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
    # Routing is gated per turn on ``routing_enabled``; a test that injects a
    # router wants it to fire, so default that flag on when a router is given.
    proposer = ChiefOfStaffProposer(
        provider=provider,
        config=config
        or ChiefOfStaffConfig(
            propose_enabled=True,
            propose_model="example-small-001",
            routing_enabled=role_router is not None,
            routing_model="example-small-001",
        ),
        conversation_repo=conv_repo,
        turn_repo=turn_repo,
        proposal_repo=proposal_repo,
        approval_store=approval_store,
        clock=FakeClock(start=START),
        role_router=role_router,
        provider_registry=provider_registry,
        config_resolver=config_resolver,
    )
    return proposer, conv_repo, turn_repo, proposal_repo, approval_store


# The repo doubles live in ``tests._shared.conversation_fakes`` now; they are
# re-exported here (explicitly, for mypy's no-implicit-reexport) so the suites
# that already import them from this module keep working.
__all__ = [
    "START",
    "FakeConversationRepo",
    "FakeProposalRepo",
    "FakeTurnRepo",
    "ProposeArgs",
    "build_proposer",
    "build_registry",
    "make_identity",
]
