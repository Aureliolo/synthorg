"""Module-level named constants for the living-documentation engine.

Numeric constants live here (annotated ``Final`` form) rather than in
``settings/definitions/`` because they are not operator-tunable: the
chunker output is part of the on-disk + RAG-index contract, so a
runtime change to the chunk target would silently invalidate previously
written index entries. The ``scripts/check_no_magic_numbers.py`` gate
allow-lists this exact pattern (module-level ``NAME: Final[int] = ...``).

If a future requirement makes any of these legitimately tunable, move
that single constant to ``settings/definitions/`` with a setting
definition + a re-index hook on change.
"""

from typing import Final

from synthorg.core.types import NotBlankStr

DOCS_CHUNK_TARGET_TOKENS: Final[int] = 256
"""Target chunk size used by :class:`DocChunker` when merging adjacent
small prose blocks. Approximate; the chunker counts characters when no
tokenizer is wired."""

DOCS_CHUNK_MAX_TOKENS: Final[int] = 512
"""Hard upper bound on a single chunk's text length (in tokens or
character-proxy units when no tokenizer is wired). Oversized blocks
are split at sentence boundaries."""

DOCS_SEARCH_DEFAULT_LIMIT: Final[int] = 8
"""Default ``limit`` argument for :meth:`DocsService.search`."""

DOCS_SEARCH_MAX_LIMIT: Final[int] = 64
"""Maximum ``limit`` accepted by :meth:`DocsService.search`. Above this
the service rejects the call to keep retrieval latency bounded."""

DOCS_BRANCH_NAME: Final[NotBlankStr] = NotBlankStr("synthorg/docs")
"""Project-workspace branch on which doc writes commit. Distinct from
``main`` so doc churn never mixes with code commits in linear history."""

DOCS_WORKSPACE_SUBDIR: Final[NotBlankStr] = NotBlankStr(".synthorg/docs")
"""Relative path under the project workspace where doc JSON lives.
Hidden by the leading dot so brownfield repos don't accidentally show
this in their default file tree views."""

DOCS_SLUG_TAG_PREFIX: Final[NotBlankStr] = NotBlankStr("doc_slug:")
"""Prefix for the per-doc index tag carried on every chunk. The indexer
uses this to delete prior chunks for the same doc before re-storing
fresh ones, achieving idempotent re-index."""

DOCS_PROJECT_TAG_PREFIX: Final[NotBlankStr] = NotBlankStr("project:")
"""Prefix for the per-project index tag carried on every chunk. Lets
retrieval scope to one project without per-project namespace routing in
the composite memory backend (one fixed namespace, project_id lives in
the tag)."""

DOCS_TYPE_TAG_PREFIX: Final[NotBlankStr] = NotBlankStr("doc_type:")
"""Prefix for the doc_type index tag carried on every chunk. Lets
search results expose the taxonomy bucket without a per-hit repository
lookup, and lets filtered RAG queries narrow by doc_type at the
backend level."""

DOCS_MEMORY_NAMESPACE: Final[NotBlankStr] = NotBlankStr("project_docs")
"""Single fixed namespace for every PROJECT_DOC memory entry. Composite
backend wiring maps this namespace to the durable backend; per-project
scoping is achieved by the ``project:<id>`` tag on each entry's
metadata, not by per-project namespaces."""

SYSTEM_DOCS_AGENT_ID: Final[NotBlankStr] = NotBlankStr("_system:docs")
"""Synthetic agent_id under which PROJECT_DOC memory entries are
stored. The leading underscore + colon disambiguates from any real
agent identifier (real agents use UUID strings)."""

DOCS_SLUG_MAX_LENGTH: Final[int] = 128
"""Cap on derived slugs to keep filesystem and URL paths bounded."""

DOCS_HISTORY_DEFAULT_LIMIT: Final[int] = 50
"""Default number of history entries returned by :meth:`DocsService.history`."""

DOCS_LIST_DEFAULT_LIMIT: Final[int] = 100
"""Default page size for :meth:`DocsService.list_docs`."""
