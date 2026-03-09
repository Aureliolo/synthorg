"""Classification result models for the error taxonomy pipeline.

Defines severity levels, individual error findings, and aggregated
classification results produced by the detection pipeline.
"""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

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
        turn_range: Optional (start, end) turn indices where the
            error was observed.
    """

    model_config = ConfigDict(frozen=True)

    category: ErrorCategory = Field(description="Error taxonomy category")
    severity: ErrorSeverity = Field(description="Severity level")
    description: NotBlankStr = Field(description="Error description")
    evidence: tuple[str, ...] = Field(
        default=(),
        description="Supporting evidence from conversation",
    )
    turn_range: tuple[int, int] | None = Field(
        default=None,
        description="Turn index range (start, end) where error observed",
    )


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
    classified_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Classification timestamp",
    )

    @computed_field(description="Number of findings")  # type: ignore[prop-decorator]
    @property
    def finding_count(self) -> int:
        """Total number of detected findings."""
        return len(self.findings)

    @computed_field(description="Whether any findings exist")  # type: ignore[prop-decorator]
    @property
    def has_findings(self) -> bool:
        """Whether any error findings were detected."""
        return len(self.findings) > 0
