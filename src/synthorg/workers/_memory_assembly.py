# module-kind: service
"""Memory collaborators the boot ``AgentEngine`` is assembled with.

Kept beside :mod:`synthorg.workers._engine_assembly` rather than inside
it so the engine-assembly module stays within its size budget, and so
the memory seam, which decides what an agent recalls before it acts, is
reviewable on its own.
"""

from typing import TYPE_CHECKING

from synthorg.core.text_estimation import DefaultTokenEstimator
from synthorg.memory.consolidation.wiki_export import WikiExporter
from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.memory.injection_factory import build_memory_injection_strategy
from synthorg.memory.shared_store import OrgSharedKnowledgeStore
from synthorg.memory.state import MemoryStateSlice, org_memory_backend_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState


def wiki_exporter_or_none(app_state: AppState) -> WikiExporter | None:
    """Build the wiki exporter backing ``memory.browse_wiki``.

    Returns:
        The exporter, or ``None`` when no memory backend is wired.
    """
    backend = app_state.slice(MemoryStateSlice).backend
    if backend is None:
        return None
    return WikiExporter(
        backend=backend,
        config=app_state.config.memory.consolidation.wiki_export,
    )


def build_memory_injection_strategy_or_none(
    app_state: AppState,
) -> MemoryInjectionStrategy | None:
    """Build the strategy that seeds memory into an agent's context.

    This is the seam the whole feature turns on: it is the one memory
    argument the engine was never constructed with, so
    ``_retrieve_injected_memory_messages`` short-circuited on every task
    and no agent ever saw a memory it had not explicitly asked for.

    The org layer arrives through ``shared_store``: without it the
    retrieval config's ``include_shared`` has nothing to include, so
    company-wide knowledge reaches an agent only if it thinks to call a
    Knowledge-Architect tool.

    Returns:
        The strategy, or ``None`` when no memory backend is wired, in
        which case the engine keeps its no-injection behaviour.
    """
    backend = app_state.slice(MemoryStateSlice).backend
    if backend is None:
        return None
    org_backend = org_memory_backend_of(app_state)
    return build_memory_injection_strategy(
        app_state.config.memory.retrieval,
        backend=backend,
        token_estimator=DefaultTokenEstimator(),
        shared_store=(
            OrgSharedKnowledgeStore(org_backend) if org_backend is not None else None
        ),
    )


__all__ = [
    "build_memory_injection_strategy_or_none",
    "wiki_exporter_or_none",
]
