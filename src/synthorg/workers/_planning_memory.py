# module-kind: code
"""Planning-session memory grant resolution for the coordinator assembly.

Resolves the owner-run planning session's memory recall grant: the agent- and
org-memory backends that back its read-only ``search_memory`` tool, plus the
context-injection strategy that pre-seeds an org/retro digest into the planning
brief. Kept separate from :mod:`synthorg.workers._coordinator_assembly` so the
coordination-assembly module stays within its size budget.
"""

from typing import TYPE_CHECKING

from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.memory.org.protocol import OrgMemoryBackend
from synthorg.memory.protocol import MemoryBackend
from synthorg.settings.state import config_resolver_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState


async def build_planning_memory(
    app_state: AppState,
) -> tuple[
    MemoryInjectionStrategy | None,
    int,
    MemoryBackend | None,
    OrgMemoryBackend | None,
]:
    """Resolve the planning session's memory grant and digest.

    Gated by ``memory.planning_memory_recall_enabled`` and the presence of an
    agent-memory backend. When on, it returns the backends for the recall tool
    grant plus a context-injection strategy pre-seeding the org/retro digest
    (when the digest budget is positive), so a plan builds on prior learnings.

    Returns:
        A ``(planning_memory, digest_budget, memory_backend, org_backend)``
        tuple; all-off / all-``None`` when recall is disabled or unwired.
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
        return None, 0, None, None
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
    return planning_memory, digest_budget, memory_backend, org_backend
