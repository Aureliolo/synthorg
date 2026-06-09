"""Typed argument models for memory tool wrappers.

Distinct from the self-editing-memory args (those live in
``synthorg.memory.self_editing_args``).  This module covers the
``memory.search`` / ``memory.recall`` retrieval tools and the
``KnowledgeArchitect*`` org-memory tools.

Tools wired to consume these models:

* :class:`~synthorg.memory.tools.search.SearchMemoryTool`
  -> :class:`SearchMemoryArgs`
* :class:`~synthorg.memory.tools.recall_search.RecallMemoryTool`
  -> :class:`RecallMemoryArgs`
* :class:`~synthorg.memory.tools.recall.RecallMemoryReadTool`
  -> :class:`RecallMemoryReadArgs`
* :class:`~synthorg.memory.tools.recall.RecallMemoryWriteTool`
  -> :class:`RecallMemoryWriteArgs`
* :class:`~synthorg.memory.tools.KnowledgeArchitectGuideTool`
  -> :class:`KnowledgeArchitectGuideArgs`
* :class:`~synthorg.memory.tools.KnowledgeArchitectSearchTool`
  -> :class:`KnowledgeArchitectSearchArgs`
* :class:`~synthorg.memory.tools.KnowledgeArchitectReadTool`
  -> :class:`KnowledgeArchitectReadArgs`
* :class:`~synthorg.memory.tools.KnowledgeArchitectWriteTool`
  -> :class:`KnowledgeArchitectWriteArgs`
* :class:`~synthorg.memory.tools.KnowledgeArchitectDeleteTool`
  -> :class:`KnowledgeArchitectDeleteArgs`
* :class:`~synthorg.memory.tools.KnowledgeArchitectBrowseWikiTool`
  -> :class:`KnowledgeArchitectBrowseWikiArgs`

Note: the four ``Core/Archival/Recall*`` tool wrappers
(``CoreMemoryReadTool`` etc. in ``synthorg/memory/tools/core.py``,
``archival.py``, ``recall.py``) re-use the
``CoreMemoryReadArgs`` / ``CoreMemoryWriteArgs`` /
``ArchivalMemorySearchArgs`` / ``ArchivalMemoryWriteArgs`` models that
already live in ``synthorg.memory.self_editing_args``.  They aren't
re-exported from here so we don't double-define the discriminator.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


_MAX_MEMORY_ID_LEN: Final[int] = 256


# ── Tool-based retrieval (search_memory / recall_memory) ────────────


class SearchMemoryArgs(BaseModel):
    """Args for ``search_memory``."""

    model_config = _ARGS_CONFIG

    query: NotBlankStr = Field(description="Natural language search query")
    categories: tuple[MemoryCategory, ...] = Field(
        default=(),
        description="Optional category filter",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum results to return",
    )


class RecallMemoryArgs(BaseModel):
    """Args for ``recall_memory`` (single-ID retrieval)."""

    model_config = _ARGS_CONFIG

    memory_id: NotBlankStr = Field(
        max_length=_MAX_MEMORY_ID_LEN,
        description="Exact memory ID to recall",
    )


# ── Recall (read/write episodic) ───────────────────────────────────


class RecallMemoryReadArgs(BaseModel):
    """Args for the ``recall_memory_read`` tool wrapper."""

    model_config = _ARGS_CONFIG

    memory_id: NotBlankStr = Field(
        max_length=_MAX_MEMORY_ID_LEN,
        description="Exact memory ID to retrieve",
    )


class RecallMemoryWriteArgs(BaseModel):
    """Args for the ``recall_memory_write`` tool wrapper."""

    model_config = _ARGS_CONFIG

    content: NotBlankStr = Field(
        max_length=50_000,
        description="Episodic event or experience to record",
    )


# ── Knowledge-architect org-memory tools ────────────────────────────


class KnowledgeArchitectGuideArgs(BaseModel):
    """Args for ``memory.guide``: no fields needed."""

    model_config = _ARGS_CONFIG


class KnowledgeArchitectSearchArgs(BaseModel):
    """Args for ``memory.search``."""

    model_config = _ARGS_CONFIG

    query: NotBlankStr = Field(description="Org memory search query")
    category: NotBlankStr | None = Field(
        default=None,
        description="Optional org-fact category filter",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum results to return",
    )


class KnowledgeArchitectReadArgs(BaseModel):
    """Args for ``memory.read``."""

    model_config = _ARGS_CONFIG

    entry_id: NotBlankStr = Field(description="Org memory entry ID")


class KnowledgeArchitectWriteArgs(BaseModel):
    """Args for ``memory.write``.

    ``category`` is typed as ``NotBlankStr`` (not the
    :class:`~synthorg.core.memory_enums.MemoryCategory` enum used by
    :class:`~synthorg.memory.self_editing_args.ArchivalMemoryWriteArgs`)
    because org-fact categories are config-driven (the runtime
    allowlist comes from the architect's role config, not a static
    enum).  The handler enforces ``category in
    config.allowed_org_fact_categories``; the model only enforces
    non-blank.
    """

    model_config = _ARGS_CONFIG

    content: NotBlankStr = Field(max_length=100_000, description="Org memory content")
    category: NotBlankStr = Field(description="Org-fact category")
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=50,
        description="Tags for the entry",
    )


class KnowledgeArchitectDeleteArgs(BaseModel):
    """Args for ``memory.delete``."""

    model_config = _ARGS_CONFIG

    entry_id: NotBlankStr = Field(description="Org memory entry ID to archive")


class KnowledgeArchitectBrowseWikiArgs(BaseModel):
    """Args for ``memory.browse_wiki``."""

    model_config = _ARGS_CONFIG

    include_raw: bool = Field(
        default=False,
        description="Include raw entries in the wiki output",
    )


__all__ = [
    "KnowledgeArchitectBrowseWikiArgs",
    "KnowledgeArchitectDeleteArgs",
    "KnowledgeArchitectGuideArgs",
    "KnowledgeArchitectReadArgs",
    "KnowledgeArchitectSearchArgs",
    "KnowledgeArchitectWriteArgs",
    "RecallMemoryArgs",
    "RecallMemoryReadArgs",
    "RecallMemoryWriteArgs",
    "SearchMemoryArgs",
]
