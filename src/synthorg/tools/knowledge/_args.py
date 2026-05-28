"""Typed argument models for the knowledge agent tools."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from synthorg.core.enums import SourceType
from synthorg.knowledge.constants import (
    KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
    KNOWLEDGE_SEARCH_MAX_LIMIT,
)

_QueryText = Annotated[str, StringConstraints(min_length=1, max_length=2048)]
_UriText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
_TitleText = Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class SearchKnowledgeArgs(BaseModel):
    """Arguments for ``search_knowledge``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    query: _QueryText = Field(description="Natural-language search text")
    limit: int = Field(
        default=KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
        ge=1,
        le=KNOWLEDGE_SEARCH_MAX_LIMIT,
        description="Maximum cited hits to return",
    )


class IngestKnowledgeArgs(BaseModel):
    """Arguments for ``ingest_knowledge``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_type: SourceType = Field(description="Origin of the source to ingest")
    uri: _UriText = Field(description="Source URI (path / url / repo@ref / id)")
    title: _TitleText = Field(description="Human-readable source title")
