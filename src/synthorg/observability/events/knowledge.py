"""Structured event-name constants for the knowledge substrate.

Event names follow the ``knowledge.<area>.<outcome>`` convention so the
sink pipeline can filter by prefix. Lifecycle transitions are logged at
INFO after the persistence write; failures at WARNING/ERROR with safe
context before raising.
"""

from typing import Final

# -- Source ingestion lifecycle -----------------------------------------------

KNOWLEDGE_SOURCE_INGESTED: Final[str] = "knowledge.source.ingested"
"""Emitted at INFO after a source is ingested and its row persisted."""

KNOWLEDGE_SOURCE_STALE: Final[str] = "knowledge.source.stale"
"""Emitted at INFO when a source is marked stale (content hash drift)."""

KNOWLEDGE_INGEST_FAILED: Final[str] = "knowledge.ingest.failed"
"""Emitted at WARNING when ingestion fails; the source row is marked FAILED."""

KNOWLEDGE_REINDEX_COMPLETED: Final[str] = "knowledge.reindex.completed"
"""Emitted at INFO after a re-index, with embedded / removed chunk counts."""

KNOWLEDGE_SOURCE_UNCHANGED: Final[str] = "knowledge.source.unchanged"
"""Emitted at INFO when a re-ingest short-circuits (source hash unchanged)."""

# -- Indexer ------------------------------------------------------------------

KNOWLEDGE_CHUNKS_INDEXED: Final[str] = "knowledge.chunks.indexed"
"""Emitted at INFO after chunks are written to the memory backend."""

KNOWLEDGE_CHUNKS_INDEX_FAILED: Final[str] = "knowledge.chunks.index_failed"
"""Emitted at WARNING when the indexer fails a delete-prior or store phase."""

# -- Retrieval ----------------------------------------------------------------

KNOWLEDGE_SEARCHED: Final[str] = "knowledge.searched"
"""Emitted at DEBUG after a knowledge search resolves hits + citations."""

KNOWLEDGE_SEARCH_FAILED: Final[str] = "knowledge.search_failed"
"""Emitted at WARNING when a knowledge search or citation resolution fails."""

KNOWLEDGE_CITATION_UNRESOLVED: Final[str] = "knowledge.citation.unresolved"
"""Emitted at WARNING when a hit's chunk id has no provenance row."""

# -- Loaders ------------------------------------------------------------------

KNOWLEDGE_SOURCE_LOADED: Final[str] = "knowledge.source.loaded"
"""Emitted at DEBUG after a loader produces a RawDocument."""

KNOWLEDGE_LOAD_FAILED: Final[str] = "knowledge.load.failed"
"""Emitted at WARNING when a loader cannot produce a RawDocument."""

KNOWLEDGE_SOURCE_FILE_SKIPPED: Final[str] = "knowledge.source.file_skipped"
"""Emitted at DEBUG when the repo loader skips a file (binary/decode/OS error)
so operators can see why files are missing from an ingested corpus."""

# -- Service-layer state transitions ------------------------------------------

KNOWLEDGE_SOURCE_NOT_FOUND: Final[str] = "knowledge.source.not_found"
"""Emitted at WARNING when a lookup by source id misses (before raise)."""

KNOWLEDGE_SOURCE_DELETED: Final[str] = "knowledge.source.deleted"
"""Emitted at INFO after a source row and its provenance are purged."""

KNOWLEDGE_SOURCE_PURGED: Final[str] = "knowledge.source.purged"
"""Emitted at INFO after :meth:`KnowledgeIndexer.purge_source` finishes, with
the count of provenance rows removed."""

KNOWLEDGE_REINDEX_STARTED: Final[str] = "knowledge.reindex.started"
"""Emitted at DEBUG when a force-reindex is requested."""

KNOWLEDGE_LIST_REQUESTED: Final[str] = "knowledge.list.requested"
"""Emitted at DEBUG when the list endpoint is called, with filter params."""

KNOWLEDGE_SUBSTRATE_UNAVAILABLE: Final[str] = "knowledge.substrate.unavailable"
"""Emitted at WARNING when :func:`_wire_knowledge_engine` cannot wire the
knowledge service (missing persistence / memory backend / dependency)."""
