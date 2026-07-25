# module-kind: code
"""Resolve who speaks for an initiative, and on which provider.

Both initiative-level sessions (the SHIP retrospective and the evaluate stage)
need the same answer to the same question: which identity is accountable for
this project, and which completion client does it run on. Sharing one resolution
keeps them from drifting into two different notions of "the lead".

Neither resolver fabricates: an initiative with no resolvable identity, or no
resolvable provider, yields ``None`` and the caller declines to run rather than
running as nobody or dispatching to an arbitrary provider.
"""

from functools import cmp_to_key

from synthorg.core.agent import AgentIdentity
from synthorg.core.authority import compare_authority
from synthorg.core.project import Project
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.protocol import CompletionProvider, ProviderSelector

logger = get_logger(__name__)


async def resolve_initiative_lead(
    registry: AgentRegistryService,
    project: Project,
) -> AgentIdentity | None:
    """Resolve the identity accountable for *project*.

    The lead is the natural answer; when a project somehow carries no lead, the
    most senior team member stands in, so an owned initiative always has an
    accountable voice.

    Returns:
        The lead identity, a senior team stand-in, or ``None`` when neither
        resolves.
    """
    if project.lead is not None:
        lead = await registry.get(project.lead)
        if lead is not None:
            return lead
    if not project.team:
        return None
    members = await registry.get_by_ids(project.team)
    if not members:
        return None
    authority_key = cmp_to_key(compare_authority)
    return max(
        members.values(),
        key=lambda agent: (authority_key(agent.role), str(agent.id)),
    )


def resolve_lead_provider(
    selector: ProviderSelector,
    lead: AgentIdentity,
    *,
    default_provider: CompletionProvider | None,
    skipped_event: str,
) -> CompletionProvider | None:
    """Resolve the completion client an initiative session runs on.

    Args:
        selector: Resolves the client for the lead's bound provider.
        lead: The identity the session runs as.
        default_provider: The explicit system default, used when the lead's
            own provider is unregistered.
        skipped_event: Event name to log the fallback under, so each caller's
            observability stays in its own event family.

    Returns:
        The lead's bound provider, the explicit system default when that is
        unregistered, or ``None`` when neither resolves.
    """
    try:
        return selector(lead)
    except DriverNotRegisteredError:
        # WARNING, not DEBUG: this silently runs the judgement (or the
        # retrospective) on a different model from the one bound to the
        # accountable lead, and a persistently misconfigured lead should not
        # need debug logging to be discoverable.
        logger.warning(
            skipped_event,
            lead_id=str(lead.id),
            reason="lead_provider_unregistered_using_default",
        )
        return default_provider
