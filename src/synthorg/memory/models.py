"""Memory domain models.

Frozen Pydantic models for memory storage requests, entries, and
queries.  ``MemoryStoreRequest`` is what callers pass to ``store()``;
``MemoryEntry`` is what comes back from ``retrieve()``.
"""

from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.utils import deduplicate_tags
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_CONTENT_REDACTED,
    MEMORY_MODEL_INVALID,
)

logger = get_logger(__name__)


def _redacted(value: NotBlankStr, *, model: str) -> NotBlankStr:
    """Strip credentials and personal data out of candidate memory text.

    Enforced on the request models rather than inside a backend so no
    write path can be added later that bypasses it: memory is written
    from agent tools, capture hooks, consolidation and the org layer,
    and every one of them constructs one of these models.

    Returns:
        The text cleared for storage.
    """
    from synthorg.memory.redaction import redact_for_memory  # noqa: PLC0415

    result = redact_for_memory(value)
    if result.redacted:
        # Finding names only. Logging the matched text would move the
        # secret from the memory store into the log.
        logger.warning(MEMORY_CONTENT_REDACTED, model=model, findings=result.findings)
    return NotBlankStr(result.content)


class MemoryMetadata(BaseModel):
    """Metadata associated with a memory entry.

    Attributes:
        source: Origin of the memory (task ID, conversation, etc.).
        confidence: Confidence score for the memory (0.0 to 1.0).
        tags: Categorization tags for filtering.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

    @field_validator("tags", mode="after")
    @classmethod
    def _deduplicate_tags(
        cls, value: tuple[NotBlankStr, ...]
    ) -> tuple[NotBlankStr, ...]:
        """Remove duplicate tags while preserving order.

        Returns:
            Tuple of ``NotBlankStr``.
        """
        return deduplicate_tags(value)


class MemoryStoreRequest(BaseModel):
    """Input to ``MemoryBackend.store()``.

    The backend assigns ``id`` and ``created_at``; callers should not
    fabricate them.

    Attributes:
        category: Memory type category.
        namespace: Storage namespace for routing (e.g. ``"memories"``,
            ``"scratch"``).  The composite backend uses this to dispatch
            to durable vs thread-scoped backends.
        content: Memory content text.
        metadata: Associated metadata.
        expires_at: Optional expiration timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    category: MemoryCategory = Field(description="Memory type category")
    namespace: NotBlankStr = Field(
        default="default",
        description="Storage namespace for composite routing",
    )
    content: NotBlankStr = Field(description="Memory content text")
    metadata: MemoryMetadata = Field(
        default_factory=MemoryMetadata,
        description="Associated metadata",
    )
    expires_at: AwareDatetime | None = Field(
        default=None,
        description="Optional expiration timestamp",
    )

    @field_validator("content", mode="after")
    @classmethod
    def _redact_content(cls, value: NotBlankStr) -> NotBlankStr:
        """Mask secrets and personal data before the text can be stored.

        Returns:
            The storable text.
        """
        return _redacted(value, model="MemoryStoreRequest")


class MemoryUpdateRequest(BaseModel):
    """Input to ``MemoryBackend.update()``.

    At least one of ``content``, ``metadata``, ``expires_at``, or
    ``clear_expiration`` must be set; a request touching nothing is
    rejected as a no-op.  ``expires_at`` and ``clear_expiration`` are
    mutually exclusive: set ``expires_at`` to change the expiration,
    ``clear_expiration=True`` to remove it, or leave both at their
    defaults to leave the existing expiration untouched.

    Attributes:
        content: New memory content, or ``None`` to leave unchanged.
        metadata: New metadata, or ``None`` to leave unchanged.
        expires_at: New expiration timestamp, or ``None`` to leave
            the current expiration untouched.
        clear_expiration: If ``True``, remove any existing expiration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    content: NotBlankStr | None = Field(
        default=None,
        description="New memory content text",
    )
    metadata: MemoryMetadata | None = Field(
        default=None,
        description="New metadata",
    )
    expires_at: AwareDatetime | None = Field(
        default=None,
        description="New expiration timestamp",
    )
    clear_expiration: bool = Field(
        default=False,
        description="Remove any existing expiration",
    )

    @field_validator("content", mode="after")
    @classmethod
    def _redact_content(cls, value: NotBlankStr | None) -> NotBlankStr | None:
        """Mask secrets and personal data before the text can be stored.

        Returns:
            The storable text, or ``None`` when content is unchanged.
        """
        if value is None:
            return None
        return _redacted(value, model="MemoryUpdateRequest")

    @model_validator(mode="after")
    def _validate_update_request(self) -> Self:
        """Ensure the request is neither contradictory nor a no-op.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.expires_at is not None and self.clear_expiration:
            msg = "expires_at and clear_expiration are mutually exclusive"
            logger.warning(
                MEMORY_MODEL_INVALID,
                model="MemoryUpdateRequest",
                field="expires_at",
                reason=msg,
            )
            raise ValueError(msg)
        if (
            self.content is None
            and self.metadata is None
            and self.expires_at is None
            and not self.clear_expiration
        ):
            msg = "MemoryUpdateRequest requires at least one field to update"
            logger.warning(
                MEMORY_MODEL_INVALID,
                model="MemoryUpdateRequest",
                field="(all)",
                reason=msg,
            )
            raise ValueError(msg)
        return self


class MemoryEntry(BaseModel):
    """A memory entry returned from the backend.

    Attributes:
        id: Unique memory identifier (assigned by backend).
        agent_id: Owning agent identifier.
        namespace: Storage namespace (routing key for composite backend).
        category: Memory type category.
        content: Memory content text.
        metadata: Associated metadata.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        expires_at: Optional expiration timestamp.
        relevance_score: Relevance score set by backend on retrieval.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique memory identifier")
    agent_id: NotBlankStr = Field(description="Owning agent identifier")
    namespace: NotBlankStr = Field(
        default="default",
        description="Storage namespace for composite routing",
    )
    category: MemoryCategory = Field(description="Memory type category")
    content: NotBlankStr = Field(description="Memory content text")
    metadata: MemoryMetadata = Field(
        default_factory=MemoryMetadata,
        description="Associated metadata",
    )
    created_at: AwareDatetime = Field(description="Creation timestamp")
    updated_at: AwareDatetime | None = Field(
        default=None,
        description="Last update timestamp",
    )
    expires_at: AwareDatetime | None = Field(
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
        """Ensure ``updated_at >= created_at`` and ``expires_at >= created_at``.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.updated_at is not None and self.updated_at < self.created_at:
            msg = (
                f"updated_at ({self.updated_at}) must be "
                f">= created_at ({self.created_at})"
            )
            logger.warning(
                MEMORY_MODEL_INVALID,
                model="MemoryEntry",
                field="updated_at",
                reason=msg,
            )
            raise ValueError(msg)
        if self.expires_at is not None and self.expires_at < self.created_at:
            msg = (
                f"expires_at ({self.expires_at}) must be "
                f">= created_at ({self.created_at})"
            )
            logger.warning(
                MEMORY_MODEL_INVALID,
                model="MemoryEntry",
                field="expires_at",
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
        namespaces: Filter by storage namespaces.
        categories: Filter by memory categories.
        tags: Filter by tags (AND semantics).
        min_relevance: Minimum relevance score threshold.
        limit: Maximum number of results.
        since: Only memories created at or after this timestamp.
        until: Only memories created before this timestamp.
        include_superseded: Whether beliefs an agent has explicitly
            replaced may be returned.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    text: NotBlankStr | None = Field(
        default=None,
        description="Semantic search text",
    )
    namespaces: frozenset[NotBlankStr] | None = Field(
        default=None,
        description="Filter by storage namespaces",
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
    since: AwareDatetime | None = Field(
        default=None,
        description="Only memories created at or after this timestamp",
    )
    until: AwareDatetime | None = Field(
        default=None,
        description="Only memories created before this timestamp",
    )
    include_superseded: bool = Field(
        default=False,
        description=(
            "Whether replaced beliefs may be returned. Excluding them is "
            "the default because a stale belief coexisting with its "
            "correction, with nothing arbitrating between them, is the "
            "failure the write-time gate exists to prevent. Audit and "
            "history views opt back in."
        ),
    )
    oldest_first: bool = Field(
        default=False,
        description=(
            "Order a metadata-only listing oldest-first instead of the "
            "default newest-first. Cap enforcement evicts the oldest, so "
            "it must see them first; ignored once ranking (text/embedding) "
            "orders the result by relevance."
        ),
    )

    @field_validator("tags", mode="after")
    @classmethod
    def _deduplicate_tags(
        cls, value: tuple[NotBlankStr, ...]
    ) -> tuple[NotBlankStr, ...]:
        """Remove duplicate tags while preserving order.

        Returns:
            Tuple of ``NotBlankStr``.
        """
        return deduplicate_tags(value)

    @model_validator(mode="after")
    def _validate_time_range(self) -> Self:
        """Ensure ``since`` is strictly before ``until`` when both are set.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if (
            self.since is not None
            and self.until is not None
            and self.since >= self.until
        ):
            msg = "since must be before until"
            logger.warning(
                MEMORY_MODEL_INVALID,
                model="MemoryQuery",
                field="since/until",
                since=str(self.since),
                until=str(self.until),
                reason=msg,
            )
            raise ValueError(msg)
        return self
