"""Pydantic args models for knowledge-substrate MCP tools."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import SourceType
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.constants import (
    KNOWLEDGE_LIST_DEFAULT_LIMIT,
    KNOWLEDGE_LIST_MAX_LIMIT,
    KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
    KNOWLEDGE_SEARCH_MAX_LIMIT,
)


class KnowledgeSearchArgs(BaseModel):
    """Args for ``knowledge:search``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr | None = Field(
        default=None, description="Scope to a project (null searches global only)"
    )
    query: NotBlankStr = Field(description="Search text")
    limit: int = Field(
        default=KNOWLEDGE_SEARCH_DEFAULT_LIMIT, ge=1, le=KNOWLEDGE_SEARCH_MAX_LIMIT
    )


class KnowledgeIngestArgs(BaseModel):
    """Args for ``knowledge:ingest``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr | None = Field(
        default=None, description="Owning project (null ingests a global source)"
    )
    source_type: SourceType = Field(description="Origin of the source")
    uri: NotBlankStr = Field(description="Source URI (path / url / repo@ref / id)")
    title: NotBlankStr = Field(description="Human-readable source title")


class KnowledgeReindexArgs(BaseModel):
    """Args for ``knowledge:reindex``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_id: NotBlankStr = Field(description="Source to force-reindex")


class KnowledgeListArgs(BaseModel):
    """Args for ``knowledge:list``.

    The list cap (``KNOWLEDGE_LIST_MAX_LIMIT``) is larger than the search
    cap (``KNOWLEDGE_SEARCH_MAX_LIMIT``) because listing returns only
    :class:`KnowledgeSource` summary rows, whereas search returns
    embedded chunk text + citation which is far heavier per row.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr | None = Field(default=None)
    include_global: bool = Field(default=False)
    stale_only: bool = Field(default=False)
    limit: int = Field(
        default=KNOWLEDGE_LIST_DEFAULT_LIMIT, ge=1, le=KNOWLEDGE_LIST_MAX_LIMIT
    )
    offset: int = Field(default=0, ge=0)


class KnowledgeGetArgs(BaseModel):
    """Args for ``knowledge:get``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_id: NotBlankStr = Field(description="Source identifier")


class KnowledgeDeleteArgs(BaseModel):
    """Args for ``knowledge:delete``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_id: NotBlankStr = Field(description="Source to delete + purge")
