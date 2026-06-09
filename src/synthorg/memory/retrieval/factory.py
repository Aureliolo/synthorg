"""Retrieval component factories.

Creates hierarchical retrievers based on configuration.
"""

from synthorg.core.types import NotBlankStr
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval.hierarchical.default_retriever import (
    DefaultHierarchicalRetriever,
)
from synthorg.memory.retrieval.hierarchical.supervisor import (
    SupervisorRouter,
)
from synthorg.memory.retrieval.hierarchical.workers import (
    EpisodicWorker,
    ProceduralWorker,
    SemanticWorker,
)
from synthorg.memory.retrieval.protocol import (
    HierarchicalRetriever,
    RetrievalWorker,
)
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.shared import SharedKnowledgeStore
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_HIERARCHICAL_ROUTING,
)
from synthorg.providers.protocol import CompletionProvider

logger = get_logger(__name__)


def create_hierarchical_retriever(
    *,
    config: MemoryRetrievalConfig,
    backend: MemoryBackend,
    provider: CompletionProvider,
    model: NotBlankStr,
    shared_store: SharedKnowledgeStore | None = None,
) -> HierarchicalRetriever:
    """Create a hierarchical retriever with all workers wired up.

    Args:
        config: Retrieval pipeline configuration.
        backend: Memory backend for personal memories.
        provider: Completion provider for supervisor LLM calls.
        model: Model identifier for supervisor (small-tier).
        shared_store: Optional shared knowledge store.

    Returns:
        Configured hierarchical retriever ready to use.
    """
    supervisor = SupervisorRouter(
        provider=provider,
        model=model,
        max_workers_per_query=config.max_workers_per_query,
        reflective_retry_enabled=config.reflective_retry_enabled,
        max_retry_count=config.max_retry_count,
        quality_threshold=config.min_relevance,
    )
    workers: dict[str, RetrievalWorker] = {
        "semantic": SemanticWorker(
            backend=backend,
            config=config,
            shared_store=shared_store,
        ),
        "episodic": EpisodicWorker(backend=backend, config=config),
        "procedural": ProceduralWorker(backend=backend, config=config),
    }
    logger.info(
        MEMORY_HIERARCHICAL_ROUTING,
        action="factory_created",
        worker_names=list(workers.keys()),
        max_workers=config.max_workers_per_query,
        retry_enabled=config.reflective_retry_enabled,
    )
    return DefaultHierarchicalRetriever(
        supervisor=supervisor,
        workers=workers,
        config=config,
    )
