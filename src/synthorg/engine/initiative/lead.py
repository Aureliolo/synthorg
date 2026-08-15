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

from synthorg.core.agent import AgentIdentity
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

    The project's recorded lead is the only answer. The greenlight staffs one
    on every initiative it stands up, and an initiative that somehow carries
    none has nobody accountable: substituting the most senior agent who
    happened to work on it would put words in the mouth of someone the org
    never made answerable for it.

    Returns:
        The lead identity, or ``None`` when the project carries no lead or the
        recorded one is no longer on the roster.
    """
    if project.lead is None:
        return None
    return await registry.get(project.lead)


def resolve_lead_provider(
    selector: ProviderSelector,
    lead: AgentIdentity,
    *,
    skipped_event: str,
) -> CompletionProvider | None:
    """Resolve the completion client an initiative session runs on.

    The lead's own bound connection is the only answer. A provider is a
    registered connection carrying its own credentials, endpoint and quota, so
    substituting another one would run the judgement on a model nobody chose
    and bill it to a connection nobody named.

    Args:
        selector: Resolves the client for the lead's bound provider.
        lead: The identity the session runs as.
        skipped_event: Event name to log an unresolved connection under, so
            each caller's observability stays in its own event family.

    Returns:
        The lead's bound provider, or ``None`` when its connection is not
        registered and the caller must decline to run.
    """
    try:
        return selector(lead)
    except DriverNotRegisteredError:
        logger.warning(
            skipped_event,
            lead_id=str(lead.id),
            reason="lead_provider_unregistered",
            provider_name=lead.model.provider,
        )
        return None
