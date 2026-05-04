"""Domain models for procedural memory auto-generation.

Defines the failure analysis payload (input to the proposer),
the procedural memory proposal (output from the proposer), the
scope enum, and the configuration model for the pipeline.
"""

from enum import StrEnum
from typing import Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from synthorg.core.enums import TaskType  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.memory.utils import deduplicate_tags
from synthorg.observability import get_logger

logger = get_logger(__name__)


class ProceduralMemoryScope(StrEnum):
    """Scope for procedural memory skill distribution.

    Members:
        AGENT: Per-agent private (existing default).
        ROLE: Shared with agents in the same role.
        DEPARTMENT: Shared within a department.
        ORG: Organization-wide shared skill pool.
    """

    AGENT = "agent"
    ROLE = "role"
    DEPARTMENT = "department"
    ORG = "org"


class FailureAnalysisPayload(BaseModel):
    """Structured failure context for the proposer LLM.

    Built from ``RecoveryResult`` and ``ExecutionResult`` fields.
    Deliberately excludes raw conversation messages to maintain
    the privacy boundary established by ``AgentContextSnapshot``.

    Attributes:
        task_id: Failed task identifier.
        task_title: Human-readable task title.
        task_description: Full task description.
        task_type: Task type classification.
        error_message: Error that triggered recovery.
        strategy_type: Recovery strategy used.
        termination_reason: Why the execution loop stopped.
        turn_count: Number of LLM turns from the context snapshot.
        tool_calls_made: Flattened tool names from all turns
            (duplicates preserved -- repeated calls are signal).
        retry_count: Previous retry attempts for this task.
        max_retries: Maximum allowed retries.
        can_reassign: Whether the task can be reassigned.
        error_category: Classified error category (e.g. provider,
            tool, budget).  Defaults to ``"unknown"`` when no
            classification is available.
        missing_capability: What capability the agent lacked,
            if identifiable.  ``None`` when not determinable.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr = Field(description="Failed task identifier")
    task_title: NotBlankStr = Field(description="Task title")
    task_description: NotBlankStr = Field(description="Task description")
    task_type: TaskType = Field(description="Task type classification")
    error_message: NotBlankStr = Field(description="Error that triggered recovery")
    strategy_type: NotBlankStr = Field(description="Recovery strategy used")
    termination_reason: NotBlankStr = Field(
        description="Why the execution loop stopped",
    )
    turn_count: int = Field(ge=0, description="LLM turns completed")
    tool_calls_made: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Flattened tool names from all turns",
    )
    retry_count: int = Field(ge=0, description="Previous retry attempts")
    max_retries: int = Field(ge=0, description="Maximum allowed retries")
    can_reassign: bool = Field(description="Whether task can be reassigned")
    error_category: NotBlankStr = Field(
        default="unknown",
        description="Classified error category",
    )
    missing_capability: NotBlankStr | None = Field(
        default=None,
        description="What capability the agent lacked",
    )

    @model_validator(mode="after")
    def _validate_retry_bounds(self) -> Self:
        """Ensure retry_count does not exceed max_retries."""
        if self.retry_count > self.max_retries:
            msg = (
                f"retry_count ({self.retry_count}) exceeds "
                f"max_retries ({self.max_retries})"
            )
            raise ValueError(msg)
        return self


class ProceduralMemoryProposal(BaseModel):
    """Structured proposal from the proposer LLM.

    Encodes three-tier progressive disclosure:

    * **Discovery** (``discovery``, max 600 chars / ~100 tokens):
      concise summary for retrieval ranking.
    * **Activation** (``condition`` + ``action`` + ``rationale``):
      when/what/why for the agent to act on.
    * **Execution** (``execution_steps``): ordered steps the agent
      should follow when applying this knowledge.

    Attributes:
        discovery: Short summary for retrieval ranking (max 600 chars).
        condition: When to apply this procedural knowledge.
        action: What to do differently next time.
        rationale: Why this approach helps.
        execution_steps: Ordered steps for applying the knowledge
            (max 50 steps).
        confidence: Proposer's confidence in the proposal (0.0-1.0).
        tags: Semantic tags for filtering (max 20 tags).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    discovery: NotBlankStr = Field(
        max_length=600,
        description="Short summary for retrieval ranking",
    )
    condition: NotBlankStr = Field(
        max_length=2000,
        description="When to apply this knowledge",
    )
    action: NotBlankStr = Field(
        max_length=2000,
        description="What to do differently next time",
    )
    rationale: NotBlankStr = Field(
        max_length=2000,
        description="Why this approach helps",
    )
    execution_steps: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=50,
        description="Ordered steps for applying the knowledge",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Proposer confidence (0.0-1.0)",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Semantic tags for filtering",
    )

    # ── Cross-agent skill pool fields (Stage 3, #1246) ────────────
    scope: ProceduralMemoryScope = Field(
        default=ProceduralMemoryScope.AGENT,
        description="Distribution scope for this skill",
    )
    supersedes: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of proposals this one supersedes",
    )
    superseded_by: NotBlankStr | None = Field(
        default=None,
        description=("ID of proposal that superseded this (tombstone marker)"),
    )
    application_count: int = Field(
        default=0,
        ge=0,
        description="Times this proposal was successfully applied",
    )
    last_applied_at: AwareDatetime | None = Field(
        default=None,
        description="Last time an agent applied this proposal",
    )

    @field_validator("tags", mode="after")
    @classmethod
    def _deduplicate_tags(
        cls, value: tuple[NotBlankStr, ...]
    ) -> tuple[NotBlankStr, ...]:
        """Deduplicate tags and clamp at the per-record limit."""
        return deduplicate_tags(value)[:20]

    @model_validator(mode="after")
    def _validate_supersession_consistency(self) -> Self:
        """Ensure supersedes and superseded_by are not self-referential."""
        if self.superseded_by is not None and self.superseded_by in self.supersedes:
            msg = f"Cannot both supersede and be superseded by {self.superseded_by}"
            raise ValueError(msg)
        return self


class ProceduralMemoryConfig(BaseModel):
    """Configuration for procedural memory auto-generation.

    Controls whether the proposer pipeline runs after agent
    failures, which model to use, and quality thresholds.

    Attributes:
        enabled: Whether procedural memory generation is active.
        model: Model identifier for the proposer LLM call.
        temperature: Sampling temperature for the proposer.
        max_tokens: Maximum tokens for the proposer response.
        min_confidence: Discard proposals below this confidence
            (must be within the same ``[0.0, 1.0]`` range as
            ``ProceduralMemoryProposal.confidence``).
        skill_md_directory: Optional directory path for SKILL.md
            file materialization.  When set, proposals are also
            written as portable SKILL.md files for git-native
            versioning.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether procedural memory generation is active",
    )
    model: NotBlankStr = Field(
        default="example-small-001",
        description="Model identifier for the proposer LLM call",
    )
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="Sampling temperature for the proposer",
    )
    max_tokens: int = Field(
        default=1500,
        gt=0,
        description="Maximum tokens for the proposer response",
    )
    min_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Discard proposals below this confidence",
    )
    skill_md_directory: NotBlankStr | None = Field(
        default=None,
        description=(
            "Directory for SKILL.md file materialization. "
            "When set, proposals are written as portable SKILL.md files."
        ),
    )
