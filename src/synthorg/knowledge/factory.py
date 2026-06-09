"""Factory wiring for the knowledge substrate.

Builds a :class:`KnowledgeService` from a connected memory backend (the
pluggable vector store) and the persistence backend (knowledge-source +
chunk-provenance repositories). Constructed at startup by
``_wire_knowledge_engine`` once persistence is connected.
"""

from synthorg.core.clock import Clock
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.indexer import KnowledgeIndexer
from synthorg.knowledge.loaders.ticket import TicketFetcher
from synthorg.knowledge.loaders.web import HtmlFetcher
from synthorg.knowledge.retrieval import KnowledgeRetriever
from synthorg.knowledge.service import KnowledgeService
from synthorg.memory.protocol import MemoryBackend
from synthorg.persistence.protocol import PersistenceBackend


def build_knowledge_service(  # noqa: PLR0913 -- cohesive boot-time wiring
    *,
    memory_backend: MemoryBackend,
    persistence: PersistenceBackend,
    config: KnowledgeConfig,
    html_fetcher: HtmlFetcher | None = None,
    ticket_fetcher: TicketFetcher | None = None,
    clock: Clock | None = None,
) -> KnowledgeService:
    """Assemble the knowledge service from its backend collaborators.

    Args:
        memory_backend: The vector store (Mem0/InMemory) for chunk
            embeddings under the KNOWLEDGE namespace.
        persistence: Provides the knowledge-source and provenance repos.
        config: Knowledge-substrate configuration.
        html_fetcher: Governed HTTP fetcher for web sources (optional;
            absence rejects ``WEB`` ingest at the factory).
        ticket_fetcher: Governed ticket fetcher (optional; absence
            rejects ``TICKET`` ingest at the factory). Production wires
            one that routes through the governed external-API access
            tool so credential brokering, SSRF + DNS pinning, and rate
            limiting apply.
        clock: Clock seam (defaults to system clock).

    Returns:
        A wired :class:`KnowledgeService`.
    """
    indexer = KnowledgeIndexer(
        backend=memory_backend,
        provenance=persistence.knowledge_provenance,
        clock=clock,
    )
    retriever = KnowledgeRetriever(
        backend=memory_backend,
        sources=persistence.knowledge_sources,
        provenance=persistence.knowledge_provenance,
    )
    return KnowledgeService(
        sources=persistence.knowledge_sources,
        indexer=indexer,
        retriever=retriever,
        config=config,
        html_fetcher=html_fetcher,
        ticket_fetcher=ticket_fetcher,
        usage_records=persistence.knowledge_usage_records,
        clock=clock,
    )
