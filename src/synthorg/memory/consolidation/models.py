"""Memory consolidation domain models.

Frozen Pydantic models for consolidation results, archival entries,
and retention rules.
"""

from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from synthorg.core.enums import MemoryCategory  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.models import MemoryMetadata


class ConsolidationResult(BaseModel):
    """Result of a memory consolidation run.

    Attributes:
        removed_ids: IDs of removed memory entries.
        summary_id: ID of the summary entry (if created).
        archived_count: Number of entries archived.
        consolidated_count: Derived from ``len(removed_ids)``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    removed_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of removed memory entries",
    )
    summary_id: NotBlankStr | None = Field(
        default=None,
        description="ID of the summary entry (if created)",
    )
    archived_count: int = Field(
        default=0,
        ge=0,
        description="Number of entries archived",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def consolidated_count(self) -> int:
        """Number of memories consolidated (derived from ``removed_ids``)."""
        return len(self.removed_ids)


class ArchivalEntry(BaseModel):
    """An archived memory entry.

    Attributes:
        original_id: ID from the hot store.
        agent_id: Owning agent identifier.
        content: Memory content text.
        category: Memory type category.
        metadata: Associated metadata.
        created_at: Original creation timestamp.
        archived_at: When this entry was archived.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    original_id: NotBlankStr = Field(description="ID from the hot store")
    agent_id: NotBlankStr = Field(description="Owning agent identifier")
    content: NotBlankStr = Field(description="Memory content text")
    category: MemoryCategory = Field(description="Memory type category")
    metadata: MemoryMetadata = Field(
        default_factory=MemoryMetadata,
        description="Associated metadata",
    )
    created_at: AwareDatetime = Field(description="Original creation timestamp")
    archived_at: AwareDatetime = Field(description="When this entry was archived")

    @model_validator(mode="after")
    def _validate_temporal_order(self) -> Self:
        """Ensure archived_at >= created_at."""
        if self.archived_at < self.created_at:
            msg = (
                f"archived_at ({self.archived_at}) must be >= "
                f"created_at ({self.created_at})"
            )
            raise ValueError(msg)
        return self


class RetentionRule(BaseModel):
    """Per-category retention rule.

    Attributes:
        category: Memory category this rule applies to.
        retention_days: Number of days to retain memories.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    category: MemoryCategory = Field(
        description="Memory category this rule applies to",
    )
    retention_days: int = Field(
        ge=1,
        description="Number of days to retain memories",
    )
