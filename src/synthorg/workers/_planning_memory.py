# module-kind: code
"""Planning-session memory grant resolution for the coordinator assembly.

Resolves the owner-run planning session's memory recall grant: the agent- and
org-memory backends that back its read-only ``search_memory`` tool, plus the
context-injection strategy that pre-seeds an org/retro digest into the planning
brief. Kept separate from :mod:`synthorg.workers._coordinator_assembly` so the
coordination-assembly module stays within its size budget.
"""

from typing import TYPE_CHECKING, NamedTuple

from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


class PlanningMemoryGrant(NamedTuple):
    """The planning session's resolved memory grant.

    Attributes:
        planning_memory: Context-injection strategy pre-seeding the org/retro
            digest into the brief, or ``None`` when the digest is disabled.
        digest_budget: Token cap for that digest (``0`` when disabled).
        memory_backend: The owner's agent-memory backend for the recall tool,
            or ``None`` when recall is off / unwired.
        org_backend: Org memory fused into recall, or ``None`` when absent.
    """

    planning_memory: MemoryInjectionStrategy | None
    digest_budget: int
    memory_backend: MemoryBackend | None
    org_backend: OrgMemoryBackend | None


_DISABLED = PlanningMemoryGrant(
    planning_memory=None,
    digest_budget=0,
    memory_backend=None,
    org_backend=None,
)


async def build_planning_memory(app_state: AppState) -> PlanningMemoryGrant:
    """Resolve the planning session's memory grant and digest.

    Gated by ``memory.planning_memory_recall_enabled`` and the presence of an
    agent-memory backend. When on, it returns the backends for the recall tool
    grant plus a context-injection strategy pre-seeding the org/retro digest
    (when the digest budget is positive), so a plan builds on prior learnings.

    Returns:
        The resolved :class:`PlanningMemoryGrant`; fully disabled when recall is
        off or no agent-memory backend is wired.
    """
    from synthorg.memory.retrieval_config import (  # noqa: PLC0415
        MemoryRetrievalConfig,
    )
    from synthorg.memory.retriever import ContextInjectionStrategy  # noqa: PLC0415
    from synthorg.memory.shared_store import OrgSharedKnowledgeStore  # noqa: PLC0415
    from synthorg.memory.state import (  # noqa: PLC0415
        memory_backend_or_none,
        org_memory_backend_of,
    )

    resolver = config_resolver_of(app_state)
    enabled = await resolver.get_bool("memory", "planning_memory_recall_enabled")
    memory_backend: MemoryBackend | None = memory_backend_or_none(app_state)
    if not enabled or memory_backend is None:
        logger.info(
            API_APP_STARTUP,
            service="planning_memory",
            note="planning recall not granted",
            reason="disabled" if not enabled else "no_memory_backend",
        )
        return _DISABLED
    org_backend: OrgMemoryBackend | None = org_memory_backend_of(app_state)
    digest_budget = await resolver.get_int("memory", "planning_memory_digest_budget")
    planning_memory: MemoryInjectionStrategy | None = None
    if digest_budget > 0:
        planning_memory = ContextInjectionStrategy(
            backend=memory_backend,
            config=MemoryRetrievalConfig(include_shared=org_backend is not None),
            shared_store=(
                OrgSharedKnowledgeStore(org_backend)
                if org_backend is not None
                else None
            ),
        )
    logger.info(
        API_APP_STARTUP,
        service="planning_memory",
        note="planning recall granted",
        digest_budget=digest_budget,
        digest_seeded=planning_memory is not None,
        org_fused=org_backend is not None,
    )
    return PlanningMemoryGrant(
        planning_memory=planning_memory,
        digest_budget=digest_budget,
        memory_backend=memory_backend,
        org_backend=org_backend,
    )
