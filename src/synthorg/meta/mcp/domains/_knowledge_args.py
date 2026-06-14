"""Pydantic args models for knowledge-substrate MCP tools."""

from pydantic import Field

from synthorg.core.types import NotBlankStr
from synthorg.knowledge.constants import (
    KNOWLEDGE_LIST_DEFAULT_LIMIT,
    KNOWLEDGE_LIST_MAX_LIMIT,
    KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
    KNOWLEDGE_SEARCH_MAX_LIMIT,
)
from synthorg.knowledge.enums import SourceType
from synthorg.meta.mcp.domains._common_args import AdminGuardrailFields, _ArgsBase


class KnowledgeSearchArgs(_ArgsBase):
    """Args for ``knowledge:search``."""

    project_id: NotBlankStr | None = Field(
        default=None, description="Scope to a project (null searches global only)"
    )
    query: NotBlankStr = Field(description="Search text")
    limit: int = Field(
        default=KNOWLEDGE_SEARCH_DEFAULT_LIMIT, ge=1, le=KNOWLEDGE_SEARCH_MAX_LIMIT
    )


class KnowledgeIngestArgs(AdminGuardrailFields):
    """Args for ``knowledge:ingest`` (destructive admin op)."""

    project_id: NotBlankStr | None = Field(
        default=None, description="Owning project (null ingests a global source)"
    )
    source_type: SourceType = Field(description="Origin of the source")
    uri: NotBlankStr = Field(description="Source URI (path / url / repo@ref / id)")
    title: NotBlankStr = Field(description="Human-readable source title")


class KnowledgeReindexArgs(AdminGuardrailFields):
    """Args for ``knowledge:reindex`` (destructive admin op)."""

    source_id: NotBlankStr = Field(description="Source to force-reindex")


class KnowledgeListArgs(_ArgsBase):
    """Args for ``knowledge:list``.

    The list cap (``KNOWLEDGE_LIST_MAX_LIMIT``) is larger than the search
    cap (``KNOWLEDGE_SEARCH_MAX_LIMIT``) because listing returns only
    :class:`KnowledgeSource` summary rows, whereas search returns
    embedded chunk text + citation which is far heavier per row.
    """

    project_id: NotBlankStr | None = Field(default=None)
    include_global: bool = Field(default=False)
    stale_only: bool = Field(default=False)
    limit: int = Field(
        default=KNOWLEDGE_LIST_DEFAULT_LIMIT, ge=1, le=KNOWLEDGE_LIST_MAX_LIMIT
    )
    offset: int = Field(default=0, ge=0)


class KnowledgeGetArgs(_ArgsBase):
    """Args for ``knowledge:get``."""

    source_id: NotBlankStr = Field(description="Source identifier")


class KnowledgeDeleteArgs(AdminGuardrailFields):
    """Args for ``knowledge:delete`` (destructive admin op)."""

    source_id: NotBlankStr = Field(description="Source to delete + purge")
