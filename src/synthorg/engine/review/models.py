"""Review pipeline domain models.

Defines the data structures for review stage results, pipeline
results, and the review verdict enum.
"""

import copy
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class ReviewVerdict(StrEnum):
    """Verdict from a single review stage.

    Uses PASS/FAIL/SKIP (not APPROVED/REJECTED) to avoid
    confusion with ``TaskStatus`` and ``ClientFeedback``.
    """

    PASS = "pass"  # noqa: S105
    FAIL = "fail"
    SKIP = "skip"


class ReviewStageResult(BaseModel):
    """Outcome of a single review pipeline stage.

    Attributes:
        stage_name: Identifier of the review stage.
        verdict: Stage verdict (pass, fail, or skip).
        reason: Explanation for the verdict.
        duration_ms: Stage execution duration in milliseconds.
        metadata: Additional stage-specific metadata.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    stage_name: NotBlankStr = Field(
        description="Identifier of the review stage",
    )
    verdict: ReviewVerdict = Field(description="Stage verdict")
    reason: NotBlankStr | None = Field(
        default=None,
        description="Explanation for the verdict",
    )
    duration_ms: int = Field(
        default=0,
        ge=0,
        description="Stage execution duration in milliseconds",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Additional stage-specific metadata",
    )

    @model_validator(mode="after")
    def _deep_copy_metadata(self) -> Self:
        """Deep-copy metadata so the frozen model cannot be aliased.

        Returns:
            The instance with ``metadata`` deep-copied.
        """
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self


class PipelineResult(BaseModel):
    """Outcome of the entire review pipeline.

    Attributes:
        task_id: ID of the reviewed task.
        final_verdict: Overall pipeline verdict.
        stage_results: Results from each stage in execution order.
        total_duration_ms: Total pipeline duration in milliseconds.
        reviewed_at: Timestamp of pipeline completion.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr = Field(
        description="ID of the reviewed task",
    )
    final_verdict: ReviewVerdict = Field(
        description="Overall pipeline verdict",
    )
    stage_results: tuple[ReviewStageResult, ...] = Field(
        default=(),
        description="Results from each stage in execution order",
    )
    total_duration_ms: int = Field(
        default=0,
        ge=0,
        description="Total pipeline duration in milliseconds",
    )
    reviewed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of pipeline completion",
    )
