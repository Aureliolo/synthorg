"""Memory domain models.

Frozen Pydantic models for memory storage requests, entries, and
queries.  ``MemoryStoreRequest`` is what callers pass to ``store()``;
``MemoryEntry`` is what comes back from ``retrieve()``.
"""

from datetime import datetime  # noqa: TC003 — required at runtime by Pydantic
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_company.core.enums import MemoryCategory  # noqa: TC001
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)


class MemoryMetadata(BaseModel):
    """Metadata associated with a memory entry.

    Attributes:
        source: Origin of the memory (task ID, conversation, etc.).
        confidence: Confidence score for the memory (0.0 to 1.0).
        tags: Categorization tags for filtering.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    source: NotBlankStr | None = Field(
        default=None,
        description="Origin of the memory",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Categorization tags",
    )

    @model_validator(mode="after")
    def _deduplicate_tags(self) -> Self:
        """Remove duplicate tags while preserving order."""
        seen: set[str] = set()
        deduped: list[str] = []
        for tag in self.tags:
            if tag not in seen:
                seen.add(tag)
                deduped.append(tag)
        if len(deduped) != len(self.tags):
            object.__setattr__(self, "tags", tuple(deduped))
        return self


class MemoryStoreRequest(BaseModel):
    """Input to ``MemoryBackend.store()``.

    The backend assigns ``id`` and ``created_at``; callers should not
    fabricate them.

    Attributes:
        category: Memory type category.
        content: Memory content text.
        metadata: Associated metadata.
        expires_at: Optional expiration timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    category: MemoryCategory = Field(description="Memory type category")
    content: NotBlankStr = Field(description="Memory content text")
    metadata: MemoryMetadata = Field(
        default_factory=MemoryMetadata,
        description="Associated metadata",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration timestamp",
    )


class MemoryEntry(BaseModel):
    """A memory entry returned from the backend.

    Attributes:
        id: Unique memory identifier (assigned by backend).
        agent_id: Owning agent identifier.
        category: Memory type category.
        content: Memory content text.
        metadata: Associated metadata.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        expires_at: Optional expiration timestamp.
        relevance_score: Relevance score set by backend on retrieval.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(description="Unique memory identifier")
    agent_id: NotBlankStr = Field(description="Owning agent identifier")
    category: MemoryCategory = Field(description="Memory type category")
    content: NotBlankStr = Field(description="Memory content text")
    metadata: MemoryMetadata = Field(
        default_factory=MemoryMetadata,
        description="Associated metadata",
    )
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime | None = Field(
        default=None,
        description="Last update timestamp",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration timestamp",
    )
    relevance_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Relevance score set by backend on retrieval",
    )

    @model_validator(mode="after")
    def _validate_timestamps(self) -> Self:
        """Ensure ``updated_at >= created_at`` when both are set."""
        if self.updated_at is not None and self.updated_at < self.created_at:
            msg = (
                f"updated_at ({self.updated_at}) must be "
                f">= created_at ({self.created_at})"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="MemoryEntry",
                field="updated_at",
                reason=msg,
            )
            raise ValueError(msg)
        return self


class MemoryQuery(BaseModel):
    """Query parameters for ``MemoryBackend.retrieve()``.

    When ``text`` is ``None``, the backend performs metadata-only
    filtering (no semantic search).

    Attributes:
        text: Semantic search text (``None`` for metadata-only).
        categories: Filter by memory categories.
        tags: Filter by tags (AND semantics).
        min_relevance: Minimum relevance score threshold.
        limit: Maximum number of results.
        since: Only memories created after this timestamp.
        until: Only memories created before this timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    text: NotBlankStr | None = Field(
        default=None,
        description="Semantic search text",
    )
    categories: frozenset[MemoryCategory] | None = Field(
        default=None,
        description="Filter by memory categories",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Filter by tags (AND semantics)",
    )
    min_relevance: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Minimum relevance score threshold",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Maximum number of results",
    )
    since: datetime | None = Field(
        default=None,
        description="Only memories created after this timestamp",
    )
    until: datetime | None = Field(
        default=None,
        description="Only memories created before this timestamp",
    )

    @model_validator(mode="after")
    def _validate_time_range(self) -> Self:
        """Ensure ``since`` is before ``until`` when both are set."""
        if (
            self.since is not None
            and self.until is not None
            and self.since >= self.until
        ):
            msg = "since must be before until"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="MemoryQuery",
                field="since/until",
                since=str(self.since),
                until=str(self.until),
                reason=msg,
            )
            raise ValueError(msg)
        return self
