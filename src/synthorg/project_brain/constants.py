"""Module-level named constants for the long-horizon project brain.

Numeric and string constants live here (annotated ``Final`` form) rather than in
``settings/definitions/`` because they are not operator-tunable: the chunker
output and the index tags are part of the on-disk plus RAG-index contract, so a
runtime change would silently invalidate previously written index entries. The
``scripts/check_no_magic_numbers.py`` gate allow-lists this exact pattern
(module-level ``NAME: Final[int] = ...``).
"""

from typing import Final

from synthorg.core.types import NotBlankStr

BRAIN_CHUNK_TARGET_TOKENS: Final[int] = 256
"""Target chunk size used by :class:`BrainChunker` when merging adjacent small
text fields. Approximate; the chunker counts characters when no tokenizer is
wired."""

BRAIN_CHUNK_MAX_TOKENS: Final[int] = 512
"""Hard upper bound on a single chunk's text length (in tokens or
character-proxy units when no tokenizer is wired). Oversized fields are split at
sentence boundaries."""

BRAIN_CHAR_PER_TOKEN_PROXY: Final[int] = 4
"""When no tokenizer is available the chunker approximates token count by
characters divided by this proxy. A 4-char-per-token average is the rough rule
of thumb for English prose."""

BRAIN_SEARCH_DEFAULT_LIMIT: Final[int] = 8
"""Default ``limit`` argument for :meth:`ProjectBrainService.query`."""

BRAIN_SEARCH_MAX_LIMIT: Final[int] = 64
"""Maximum ``limit`` accepted by :meth:`ProjectBrainService.query`. Above this
the service rejects the call to keep retrieval latency bounded."""

BRAIN_LIST_DEFAULT_LIMIT: Final[int] = 100
"""Default page size for :meth:`ProjectBrainService.list_current`."""

BRAIN_HISTORY_DEFAULT_LIMIT: Final[int] = 50
"""Default number of revisions returned by :meth:`ProjectBrainService.history`."""

BRAIN_BRANCH_NAME: Final[NotBlankStr] = NotBlankStr("synthorg/docs")
"""Project-workspace branch on which brain snapshots commit. Shared with the
living-documentation engine so brain and doc churn live together on one branch,
distinct from ``main`` so they never mix with code commits in linear history."""

BRAIN_WORKSPACE_SUBDIR: Final[NotBlankStr] = NotBlankStr(".synthorg/brain")
"""Relative path under the project workspace where brain entry JSON lives.
Hidden by the leading dot so brownfield repos do not surface this in their
default file tree views."""

BRAIN_MEMORY_NAMESPACE: Final[NotBlankStr] = NotBlankStr("project_brain")
"""Single fixed namespace for every PROJECT_BRAIN memory entry. Per-project
scoping is achieved by the ``project:<id>`` tag on each entry, not by per-project
namespaces."""

SYSTEM_BRAIN_AGENT_ID: Final[NotBlankStr] = NotBlankStr("_system:brain")
"""Synthetic agent_id under which PROJECT_BRAIN memory entries are stored. The
leading underscore plus colon disambiguates from any real agent identifier (real
agents use UUID strings)."""

BRAIN_PROJECT_TAG_PREFIX: Final[NotBlankStr] = NotBlankStr("project:")
"""Prefix for the per-project index tag carried on every chunk. Lets retrieval
scope to one project without per-project namespace routing in the composite
memory backend."""

BRAIN_ENTRY_TAG_PREFIX: Final[NotBlankStr] = NotBlankStr("brain_entry:")
"""Prefix for the per-entry index tag carried on every chunk. The indexer uses
this to delete prior chunks for the same entry before re-storing fresh ones,
achieving idempotent re-index."""

BRAIN_KIND_TAG_PREFIX: Final[NotBlankStr] = NotBlankStr("brain_kind:")
"""Prefix for the entry-kind index tag carried on every chunk. Lets search hits
expose the kind without a per-hit repository lookup, and lets filtered RAG
queries narrow by kind at the backend level."""

BRAIN_FIRST_REVISION: Final[int] = 1
"""Revision number assigned to the first version of a logical entry. Revisions
are monotonic per ``entry_id`` and server-assigned."""

BRAIN_WRITE_ACTION_TYPE: Final[NotBlankStr] = NotBlankStr("brain:write")
"""Action-type string for the brain write tool, routed through the trust and
capability system as a write action. A custom action-type string defined here
alongside the other brain constants; the ``security.autonomy.enums.ActionType``
taxonomy accepts custom action-type strings beyond its built-in members."""
