# module-kind: tests
"""Shared in-memory doubles + builder for the proposer test suites.

Used by ``test_propose.py`` (the clarify-and-propose loop) and
``test_propose_routing.py`` (concern routing in front of it) so the two
suites share one set of repository doubles and one proposer builder.
"""

from datetime import UTC, date, datetime

from synthorg.api.approval_store import ApprovalStore
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import (
    ProposeArgs,
)
from synthorg.meta.chief_of_staff.plan_intake import ConversationalPlanDispatcher
from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
from synthorg.meta.chief_of_staff.routing import RoleRouter
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock, as_uuid
from tests._shared.conversation_fakes import (
    FakeConversationRepo,
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
) -> AgentIdentity:
    """Build an ``AgentIdentity`` for the proposer test suites.

    Returns:
        A registered-shaped identity with the given role and provider.
    """
    return AgentIdentity(
        id=as_uuid(name),
        name=NotBlankStr(name),
        role=NotBlankStr(role),
        department=NotBlankStr(department),
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


def build_proposer(  # noqa: PLR0913 -- test builder: independent DI knobs
    *,
    provider: ScriptedProvider,
    config: ChiefOfStaffConfig | None = None,
    role_router: RoleRouter | None = None,
    provider_registry: ProviderRegistry | None = None,
    config_resolver: ConfigResolver | None = None,
    plan_dispatcher: ConversationalPlanDispatcher | None = None,
) -> tuple[
    ChiefOfStaffProposer,
    FakeConversationRepo,
    FakeTurnRepo,
    ApprovalStore,
]:
    """Build a proposer over in-memory doubles for the test suites.

    When *plan_dispatcher* is supplied it is attached so the proposer can
    draft a plan for an accepted work brief; without it, the act path raises
    on a work brief (matching an unwired pipeline).

    Returns:
        The proposer and its conversation / turn repos and the approval
        store, so a test can inspect persisted state.
    """
    conv_repo = FakeConversationRepo()
    turn_repo = FakeTurnRepo()
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
        approval_store=approval_store,
        clock=FakeClock(start=START),
        role_router=role_router,
        provider_registry=provider_registry,
        config_resolver=config_resolver,
    )
    if plan_dispatcher is not None:
        proposer.attach_plan_dispatcher(plan_dispatcher)
    return proposer, conv_repo, turn_repo, approval_store


# The repo doubles live in ``tests._shared.conversation_fakes`` now; they are
# re-exported here (explicitly, for mypy's no-implicit-reexport) so the suites
# that already import them from this module keep working.
__all__ = [
    "START",
    "FakeConversationRepo",
    "FakeTurnRepo",
    "ProposeArgs",
    "build_proposer",
    "build_registry",
    "make_identity",
]
