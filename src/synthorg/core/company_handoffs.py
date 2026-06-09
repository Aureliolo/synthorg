# module-kind: code
"""Cross-department workflow models: handoffs and escalation paths."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.normalization import normalize_identifier
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.company import COMPANY_VALIDATION_ERROR

logger = get_logger(__name__)


def _reject_same_department(from_dept: str, to_dept: str, label: str) -> None:
    """Reject cross-department models where from and to are the same.

    Raises:
        ValueError: If *from_dept* and *to_dept* resolve to the same
            department (case-insensitive).
    """
    if normalize_identifier(from_dept) == normalize_identifier(to_dept):
        msg = (
            f"{label} must be between different departments: "
            f"{from_dept!r} == {to_dept!r}"
        )
        logger.warning(COMPANY_VALIDATION_ERROR, error=msg)
        raise ValueError(msg)


class WorkflowHandoff(BaseModel):
    """Cross-department handoff definition.

    Attributes:
        from_department: Source department name.
        to_department: Target department name.
        trigger: Condition that triggers this handoff.
        artifacts: Artifacts passed during handoff.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    from_department: NotBlankStr = Field(description="Source department")
    to_department: NotBlankStr = Field(description="Target department")
    trigger: NotBlankStr = Field(description="Trigger condition")
    artifacts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Artifacts passed during handoff",
    )

    @model_validator(mode="after")
    def _validate_different_departments(self) -> Self:
        """Reject handoffs within the same department.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the source and target departments are the same.
        """
        _reject_same_department(
            self.from_department,
            self.to_department,
            "Handoff",
        )
        return self


class EscalationPath(BaseModel):
    """Cross-department escalation path.

    Attributes:
        from_department: Source department name.
        to_department: Target department name.
        condition: Condition that triggers escalation.
        priority_boost: Priority boost applied on escalation (0-3).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    from_department: NotBlankStr = Field(description="Source department")
    to_department: NotBlankStr = Field(description="Target department")
    condition: NotBlankStr = Field(description="Escalation condition")
    priority_boost: int = Field(
        default=1,
        ge=0,
        le=3,
        description="Priority boost on escalation (0-3)",
    )

    @model_validator(mode="after")
    def _validate_different_departments(self) -> Self:
        """Reject escalations within the same department.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If the source and target departments are the same.
        """
        _reject_same_department(
            self.from_department,
            self.to_department,
            "Escalation",
        )
        return self
