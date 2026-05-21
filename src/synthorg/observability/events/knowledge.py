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
