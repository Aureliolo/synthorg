"""Memory consolidation domain models.

Frozen Pydantic models for consolidation results, archival entries,
retention rules, dual-mode archival types, and GEMS two-tier
compressed/detailed experience models.
"""

from enum import StrEnum
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


class ArchivalMode(StrEnum):
    """How a memory entry was archived during consolidation.

    Determines the preservation strategy applied before archival.
    """

    ABSTRACTIVE = "abstractive"
    """LLM-generated summary for sparse/conversational content."""

    EXTRACTIVE = "extractive"
    """Verbatim key-fact extraction for dense/factual content."""


class ArchivalModeAssignment(BaseModel):
    """Maps a removed memory entry to the archival mode applied.

    Attributes:
        original_id: ID of the removed memory entry.
        mode: Archival mode applied to this entry.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    original_id: NotBlankStr = Field(
        description="ID of the removed memory entry",
    )
    mode: ArchivalMode = Field(
        description="Archival mode applied to this entry",
    )


class ArchivalIndexEntry(BaseModel):
    """Maps a removed memory entry to its archival store ID.

    Enables deterministic index-based restore: agents can look up
    their own archived entries by original ID without semantic search.

    Attributes:
        original_id: ID of the original memory entry.
        archival_id: ID assigned by the archival store.
        mode: Archival mode used for this entry.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    original_id: NotBlankStr = Field(
        description="ID of the original memory entry",
    )
    archival_id: NotBlankStr = Field(
        description="ID assigned by the archival store",
    )
    mode: ArchivalMode = Field(
        description="Archival mode used for this entry",
    )


class ConsolidationResult(BaseModel):
    """Result of a memory consolidation run.

    Attributes:
        removed_ids: IDs of removed memory entries.
        summary_ids: IDs of summary entries created during the run.
            Strategies that produce a single summary populate a
            one-element tuple; strategies that produce per-group
            summaries (e.g. ``LLMConsolidationStrategy``) populate one
            entry per group so callers see every summary, not just the
            last one.  Callers that previously passed a scalar
            ``summary_id=`` keyword (now a derived ``@computed_field``)
            will hit a hard ``ValidationError`` because the model uses
            ``extra='forbid'`` -- no silent data loss.
        summary_id: Derived from ``summary_ids[-1]`` when any summary
            was produced, otherwise ``None``.  Kept as a
            ``@computed_field`` so callers that only need a single
            representative id keep working.
        archived_count: Number of entries archived.
        consolidated_count: Derived from ``len(removed_ids)``.
        mode_assignments: Per-entry archival mode assignments (set by
            strategy, empty for strategies that don't classify density).
        archival_index: Maps original memory IDs to archival store IDs
            (built by service after archival completes).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    removed_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of removed memory entries",
    )
    summary_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of every summary entry produced during the run",
    )
    archived_count: int = Field(
        default=0,
        ge=0,
        description="Number of entries archived",
    )
    mode_assignments: tuple[ArchivalModeAssignment, ...] = Field(
        default=(),
        description="Per-entry archival mode assignments",
    )
    archival_index: tuple[ArchivalIndexEntry, ...] = Field(
        default=(),
        description="Original-to-archival ID mapping",
    )

    @model_validator(mode="after")
    def _validate_archival_consistency(self) -> Self:
        """Ensure archival fields are internally consistent.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if len(self.removed_ids) != len(set(self.removed_ids)):
            msg = "removed_ids contains duplicates"
            raise ValueError(msg)
        if len(self.summary_ids) != len(set(self.summary_ids)):
            msg = "summary_ids contains duplicates"
            raise ValueError(msg)
        if self.archived_count > self.consolidated_count:
            msg = (
                f"archived_count ({self.archived_count}) must not exceed "
                f"consolidated_count ({self.consolidated_count})"
            )
            raise ValueError(msg)
        if len(self.archival_index) > self.archived_count:
            msg = (
                f"archival_index length ({len(self.archival_index)}) "
                f"must not exceed archived_count ({self.archived_count})"
            )
            raise ValueError(msg)
        if len(self.mode_assignments) > len(self.removed_ids):
            msg = (
                f"mode_assignments length ({len(self.mode_assignments)}) "
                f"must not exceed removed_ids length "
                f"({len(self.removed_ids)})"
            )
            raise ValueError(msg)
        removed_set = set(self.removed_ids)
        assignment_ids = [a.original_id for a in self.mode_assignments]
        if len(assignment_ids) != len(set(assignment_ids)):
            msg = "mode_assignments contains duplicate original_ids"
            raise ValueError(msg)
        if any(aid not in removed_set for aid in assignment_ids):
            msg = "mode_assignments contains original_ids not in removed_ids"
            raise ValueError(msg)
        for idx_entry in self.archival_index:
            if idx_entry.original_id not in removed_set:
                msg = (
                    f"archival_index entry '{idx_entry.original_id}' not in removed_ids"
                )
                raise ValueError(msg)
        index_ids = [e.original_id for e in self.archival_index]
        if len(index_ids) != len(set(index_ids)):
            msg = "archival_index contains duplicate original_ids"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def consolidated_count(self) -> int:
        """Number of memories consolidated (derived from ``removed_ids``)."""
        return len(self.removed_ids)

    @property
    def summary_id(self) -> NotBlankStr | None:
        """Representative summary id (last one produced, or ``None``).

        Derived from ``summary_ids``.  Callers that need every summary
        (e.g. multi-category ``LLMConsolidationStrategy`` runs) should
        read ``summary_ids`` directly.

        Exposed as a plain ``@property`` (not ``@computed_field``) so
        it is NOT emitted by ``model_dump()``.  Otherwise the serialized
        payload would include ``summary_id`` and a round-trip through
        ``model_validate(result.model_dump())`` would fail against the
        ``extra='forbid'`` guard -- a nasty surprise for any
        persistence or copy-through-JSON path.
        """
        return self.summary_ids[-1] if self.summary_ids else None


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
        archival_mode: How this entry was archived.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    archival_mode: ArchivalMode = Field(
        description="Archival mode used for this entry",
    )

    @model_validator(mode="after")
    def _validate_temporal_order(self) -> Self:
        """Ensure archived_at >= created_at.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    category: MemoryCategory = Field(
        description="Memory category this rule applies to",
    )
    retention_days: int = Field(
        ge=1,
        description="Number of days to retain memories",
    )


# ── GEMS two-tier experience models ───────────────────────────────


class ContentDensity(StrEnum):
    """Content density classification for archival mode selection.

    Attributes:
        SPARSE: Conversational, narrative, low information density.
        DENSE: Code, structured data, identifiers, high density.
    """

    SPARSE = "sparse"
    DENSE = "dense"


class DetailedExperience(BaseModel):
    """Tier 1 raw artifact -- append-only execution trace.

    Stores the complete raw execution context for a single turn or
    task.  Retrieved by ``entry.id`` only (audit/debug), NOT used
    for context injection.

    Attributes:
        id: Unique identifier.
        agent_id: Owning agent identifier.
        prompt: Raw prompt sent to the agent.
        output: Raw output produced by the agent.
        verification_feedback: Verification result text (``None``
            when no verification was performed).
        reasoning_trace: Step-by-step reasoning trace entries.
        metadata: Associated metadata (tags include
            ``"detailed_experience"``).
        created_at: When the experience was captured.
        source_task_id: Optional originating task identifier.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique identifier")
    agent_id: NotBlankStr = Field(description="Owning agent identifier")
    prompt: NotBlankStr = Field(description="Raw prompt sent to the agent")
    output: NotBlankStr = Field(
        description="Raw output produced by the agent",
    )
    verification_feedback: NotBlankStr | None = Field(
        default=None,
        description="Verification result text",
    )
    reasoning_trace: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Step-by-step reasoning trace entries",
    )
    metadata: MemoryMetadata = Field(
        default_factory=MemoryMetadata,
        description="Associated metadata",
    )
    created_at: AwareDatetime = Field(
        description="When the experience was captured",
    )
    source_task_id: NotBlankStr | None = Field(
        default=None,
        description="Optional originating task identifier",
    )

    @model_validator(mode="after")
    def _validate_tier_tag(self) -> Self:
        """Require the ``detailed_experience`` tag in metadata.tags.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if "detailed_experience" not in self.metadata.tags:
            msg = (
                "DetailedExperience.metadata.tags must contain "
                "'detailed_experience' (tier marker required by the "
                "two-tier pipeline)"
            )
            raise ValueError(msg)
        return self


class CompressedExperience(BaseModel):
    """Tier 2 compressed learning -- retrieval-primary.

    Distilled from one or more ``DetailedExperience`` entries by the
    ``ExperienceCompressor``.  These are the entries agents see during
    context injection -- raw traces are NOT injected.

    GEMS ablation shows compressed experiences outperform raw reasoning
    traces by 2.5x (GEMS section 4.3).

    Attributes:
        id: Unique identifier.
        agent_id: Owning agent identifier.
        strategic_decisions: Distilled "what worked, what didn't"
            learnings.
        applicable_contexts: When and where this experience applies.
        source_artifact_ids: Links back to ``DetailedExperience`` IDs.
        compression_ratio: ``compressed_len / raw_len`` (0.0-1.0).
        compressor_version: Version stamp for the compressor that
            produced this entry.
        metadata: Associated metadata (tags include
            ``"compressed_experience"``).
        created_at: When the compression was performed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique identifier")
    agent_id: NotBlankStr = Field(description="Owning agent identifier")
    strategic_decisions: tuple[NotBlankStr, ...] = Field(
        description="Distilled strategic learnings",
    )
    applicable_contexts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="When and where this experience applies",
    )
    source_artifact_ids: tuple[NotBlankStr, ...] = Field(
        description=(
            "Links back to DetailedExperience IDs "
            "(must contain at least one entry -- provenance required)"
        ),
    )
    compression_ratio: float = Field(
        gt=0.0,
        le=1.0,
        description="compressed_len / raw_len",
    )
    compressor_version: NotBlankStr = Field(
        description="Compressor version stamp",
    )
    metadata: MemoryMetadata = Field(
        default_factory=MemoryMetadata,
        description="Associated metadata",
    )
    created_at: AwareDatetime = Field(
        description="When the compression was performed",
    )

    @model_validator(mode="after")
    def _validate_non_empty(self) -> Self:
        """Require strategic decisions, provenance, and tier marker.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not self.strategic_decisions:
            msg = "strategic_decisions must contain at least one entry"
            raise ValueError(msg)
        if not self.source_artifact_ids:
            msg = (
                "source_artifact_ids must contain at least one entry "
                "(provenance required to prevent orphan experiences)"
            )
            raise ValueError(msg)
        if "compressed_experience" not in self.metadata.tags:
            msg = (
                "CompressedExperience.metadata.tags must contain "
                "'compressed_experience' (tier marker required by the "
                "two-tier pipeline)"
            )
            raise ValueError(msg)
        return self
