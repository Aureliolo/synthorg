# module-kind: declarative
"""Pre-flight validation result shapes for the fine-tuning pipeline.

Separate from the pipeline's domain models because these describe an ANSWER
rather than the run: they are the readiness report the dashboard renders
before anything is configured, and they live beside ``ProbeResult``, the other
value the preflight endpoint assembles.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.core.types import NotBlankStr


class PreflightCheck(BaseModel):
    """Result of a single pre-flight validation check.

    Attributes:
        name: Check identifier.
        status: Pass/warn/fail result.
        message: Human-readable result description.
        detail: Optional additional detail.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Check identifier")
    status: Literal["pass", "warn", "fail"] = Field(description="Result")
    message: NotBlankStr = Field(description="Result description")
    detail: str | None = Field(default=None, description="Additional detail")


class PreflightResult(BaseModel):
    """Aggregated pre-flight validation results.

    Attributes:
        checks: Individual check results.
        recommended_batch_size: VRAM-based batch size recommendation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    checks: tuple[PreflightCheck, ...] = Field(
        default=(),
        description="Individual check results",
    )
    recommended_batch_size: int | None = Field(
        default=None,
        ge=1,
        description="VRAM-based batch size recommendation",
    )

    @computed_field
    @property
    def can_proceed(self) -> bool:
        # This docstring is the field's OpenAPI description and reaches the
        # generated TypeScript, so it stays the one line it renders as.
        """True if no checks have ``"fail"`` status."""
        return all(c.status != "fail" for c in self.checks)
