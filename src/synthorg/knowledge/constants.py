"""Module-level named constants for the knowledge substrate.

Numeric constants live here (annotated ``Final`` form) rather than in
``settings/definitions/`` when they are part of the on-disk + RAG-index
contract: a runtime change to a chunk budget would silently invalidate
previously indexed chunks. Operator-tunable knobs live on
``KnowledgeConfig`` instead. The ``scripts/check_no_magic_numbers.py``
gate allow-lists the module-level ``NAME: Final[int] = ...`` pattern.
"""

from typing import Final

from synthorg.core.types import NotBlankStr

KNOWLEDGE_MEMORY_NAMESPACE: Final[NotBlankStr] = NotBlankStr("knowledge")
"""Single fixed namespace for every KNOWLEDGE memory entry. Per-project
and global scoping is achieved by the ``project:<id>`` / ``scope:global``
tags on each entry's metadata, not by per-project namespaces."""

SYSTEM_KNOWLEDGE_AGENT_ID: Final[NotBlankStr] = NotBlankStr("_system:knowledge")
"""Synthetic agent_id under which KNOWLEDGE memory entries are stored.
The leading underscore + colon disambiguates from real agent UUIDs and
keeps the per-agent storage abstraction intact (the per-agent retrieve
path supports BM25 sparse hybrid; the shared-store path does not)."""

KNOWLEDGE_SOURCE_TAG_PREFIX: Final[NotBlankStr] = NotBlankStr("source:")
"""Per-source index tag carried on every chunk. The indexer uses this to
delete prior chunks for the same source before re-storing, achieving
idempotent re-index."""

KNOWLEDGE_CHUNK_TAG_PREFIX: Final[NotBlankStr] = NotBlankStr("chunk:")
"""Per-chunk index tag carried on every chunk. Lets the retriever resolve
the chunk's full citation and lets re-index delete an individual chunk."""

KNOWLEDGE_PROJECT_TAG_PREFIX: Final[NotBlankStr] = NotBlankStr("project:")
"""Per-project scope tag. Project-scoped retrieval filters by this."""

KNOWLEDGE_GLOBAL_SCOPE_TAG: Final[NotBlankStr] = NotBlankStr("scope:global")
"""Marks a global (cross-project) source so retrieval can union global
hits with the active project's hits."""

KNOWLEDGE_KIND_TAG_PREFIX: Final[NotBlankStr] = NotBlankStr("kind:")
"""Content-kind tag so a search hit exposes its kind without a per-hit
repository lookup."""

KNOWLEDGE_CHUNK_TARGET_TOKENS: Final[int] = 384
"""Target chunk size used by the document and code chunkers. Approximate;
counted via the character-per-token proxy when no tokenizer is wired."""

KNOWLEDGE_CHUNK_MAX_TOKENS: Final[int] = 768
"""Hard upper bound on a single chunk's text length (token-proxy units).
Oversized units are split at the nearest structural boundary."""

KNOWLEDGE_CHAR_PER_TOKEN_PROXY: Final[int] = 4
"""Characters-per-token proxy used when no tokenizer is wired. The
4-char-per-token average is the rough rule of thumb for English prose."""

KNOWLEDGE_SEARCH_DEFAULT_LIMIT: Final[int] = 8
"""Default ``limit`` for a knowledge search."""

KNOWLEDGE_SEARCH_MAX_LIMIT: Final[int] = 64
"""Maximum ``limit`` accepted by a knowledge search, to bound latency."""

KNOWLEDGE_LIST_DEFAULT_LIMIT: Final[int] = 100
"""Default page size for listing knowledge sources."""

KNOWLEDGE_LIST_MAX_LIMIT: Final[int] = 500
"""Maximum page size accepted when listing knowledge sources. The hard
cap bounds list-controller latency and keeps the MCP schema in lockstep
with the args-model :class:`Field` validator."""

KNOWLEDGE_REINDEX_PAGE_SIZE: Final[int] = 100
"""Page size when deleting prior chunks for a source during re-index."""

KNOWLEDGE_MAX_DELETE_ITERATIONS: Final[int] = 100
"""Safety cap on delete-prior pagination passes (bounds a runaway
re-index of a pathologically large source)."""
