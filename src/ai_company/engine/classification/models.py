"""Classification result models for the error taxonomy pipeline.

Defines severity levels, individual error findings, and aggregated
classification results produced by the detection pipeline.
"""

from datetime import UTC, datetime
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

from ai_company.budget.coordination_config import ErrorCategory  # noqa: TC001
from ai_company.core.types import NotBlankStr  # noqa: TC001


class ErrorSeverity(StrEnum):
    """Severity level for a detected coordination error."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ErrorFinding(BaseModel):
    """A single coordination error detected during classification.

    Attributes:
        category: The error category from the taxonomy.
        severity: Severity level of the finding.
        description: Human-readable description of the error.
        evidence: Supporting evidence extracted from the conversation.
        turn_range: (start, end) 0-based index range where the error
            was observed, or ``None`` if the error cannot be attributed
            to specific positions.  For conversation-based detectors
            this is the message index; for turn-based detectors this
            is the index into the turns tuple.
    """

    model_config = ConfigDict(frozen=True)

    category: ErrorCategory = Field(description="Error taxonomy category")
    severity: ErrorSeverity = Field(description="Severity level")
    description: NotBlankStr = Field(description="Error description")
    evidence: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Supporting evidence from conversation",
    )
    turn_range: tuple[int, int] | None = Field(
        default=None,
        description=(
            "0-based index range (start, end) where error was observed.  "
            "For conversation-based detectors this is the message "
            "index in the conversation tuple; for turn-based "
            "detectors this is the index into the turns tuple."
        ),
    )

    @model_validator(mode="after")
    def _validate_turn_range(self) -> Self:
        if self.turn_range is not None:
            start, end = self.turn_range
            if start < 0 or end < 0:
                msg = "turn_range positions must be non-negative"
                raise ValueError(msg)
            if start > end:
                msg = f"turn_range start ({start}) must not exceed end ({end})"
                raise ValueError(msg)
        return self


class ClassificationResult(BaseModel):
    """Aggregated result from the error classification pipeline.

    Attributes:
        execution_id: Unique identifier for the execution run.
        agent_id: Agent that was executing.
        task_id: Task being executed.
        categories_checked: Which error categories were checked.
        findings: All detected error findings.
        classified_at: Timestamp when classification completed.
    """

    model_config = ConfigDict(frozen=True)

    execution_id: NotBlankStr = Field(description="Execution run identifier")
    agent_id: NotBlankStr = Field(description="Agent identifier")
    task_id: NotBlankStr = Field(description="Task identifier")
    categories_checked: tuple[ErrorCategory, ...] = Field(
        description="Categories that were checked",
    )
    findings: tuple[ErrorFinding, ...] = Field(
        default=(),
        description="Detected error findings",
    )
    classified_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Classification timestamp",
    )

    @model_validator(mode="after")
    def _validate_findings_match_categories(self) -> Self:
        checked = set(self.categories_checked)
        invalid = {f.category for f in self.findings} - checked
        if invalid:
            names = sorted(c.value for c in invalid)
            msg = f"Findings contain unchecked categories: {names}"
            raise ValueError(msg)
        return self

    @computed_field(description="Number of findings")  # type: ignore[prop-decorator]
    @property
    def finding_count(self) -> int:
        """Total number of detected findings."""
        return len(self.findings)

    @computed_field(description="Whether any findings exist")  # type: ignore[prop-decorator]
    @property
    def has_findings(self) -> bool:
        """Whether any error findings were detected."""
        return bool(self.findings)
