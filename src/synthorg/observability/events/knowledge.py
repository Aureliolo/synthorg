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

KNOWLEDGE_GRAMMAR_LOAD_FAILED: Final[str] = "knowledge.grammar.load_failed"
"""Emitted at WARNING when a tree-sitter grammar fails to load.

Distinguishes a clean "grammar not installed" fallback to the line-window
chunker from a regression in the grammar pack (corrupt data, I/O failure)
that operators would otherwise have no signal for.
"""

KNOWLEDGE_GRAMMAR_PREFETCH_FAILED: Final[str] = "knowledge.grammar.prefetch_failed"
"""Emitted at WARNING when batch grammar prefetch fails.

Non-fatal: chunking still proceeds via each unit's own lazy
``get_parser`` call, just without the batched pre-download.
"""

# -- Retrieval ----------------------------------------------------------------

KNOWLEDGE_SEARCHED: Final[str] = "knowledge.searched"
"""Emitted at DEBUG after a knowledge search resolves hits + citations."""

KNOWLEDGE_SEARCH_FAILED: Final[str] = "knowledge.search_failed"
"""Emitted at WARNING when a knowledge search or citation resolution fails."""

KNOWLEDGE_CITATION_UNRESOLVED: Final[str] = "knowledge.citation.unresolved"
"""Emitted at WARNING when a hit's chunk id has no provenance row."""

# -- Synthesis (generative RAG) -----------------------------------------------

KNOWLEDGE_SYNTHESISED: Final[str] = "knowledge.synthesised"
"""Emitted at DEBUG after a grounded answer is synthesised over retrieved
chunks, with the claim and consulted-chunk counts."""

KNOWLEDGE_SYNTHESIS_FAILED: Final[str] = "knowledge.synthesis.failed"
"""Emitted at WARNING when synthesis fails (no grounding, unparseable output,
or a claim citing an unknown chunk) before raising."""

KNOWLEDGE_SYNTHESIS_OUTPUT_INVALID: Final[str] = "knowledge.synthesis.output_invalid"
"""Emitted at WARNING when the synthesiser LLM returns unparseable or
schema-invalid structured output."""

KNOWLEDGE_SYNTHESIZER_KIND_UNKNOWN: Final[str] = "knowledge.synthesis.kind_unknown"
"""Emitted at WARNING when the synthesiser factory gets an unknown kind."""

# -- Loaders ------------------------------------------------------------------

KNOWLEDGE_SOURCE_LOADED: Final[str] = "knowledge.source.loaded"
"""Emitted at DEBUG after a loader produces a RawDocument."""

KNOWLEDGE_SOURCE_UNAVAILABLE: Final[str] = "knowledge.source.unavailable"
"""Emitted at WARNING before a loader rejects an unreachable source."""

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

KNOWLEDGE_TICKET_FETCHED: Final[str] = "knowledge.ticket.fetched"
"""Emitted at INFO when the governed ticket fetcher returns a thread."""

KNOWLEDGE_TICKET_FETCH_BLOCKED: Final[str] = "knowledge.ticket.fetch_blocked"
"""Emitted at WARNING when a ticket fetch is SSRF-blocked, returns non-2xx,
or yields a malformed payload."""

KNOWLEDGE_URI_VALIDATION_FAILED: Final[str] = "knowledge.uri.validation_failed"
"""Emitted at WARNING when a filesystem-ingestion URI cannot be resolved to a
path (path-traversal defence for REPO/PDF loaders)."""
