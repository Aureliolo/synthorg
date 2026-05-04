"""Types and constants for workflow validation."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from synthorg.core.enums import WorkflowEdgeType
from synthorg.core.types import NotBlankStr  # noqa: TC001

_MIN_SPLIT_BRANCHES = 2

_CONDITIONAL_EDGE_TYPES = frozenset(
    {
        WorkflowEdgeType.CONDITIONAL_TRUE,
        WorkflowEdgeType.CONDITIONAL_FALSE,
    }
)

_VERIFICATION_EDGE_TYPES = frozenset(
    {
        WorkflowEdgeType.VERIFICATION_PASS,
        WorkflowEdgeType.VERIFICATION_FAIL,
        WorkflowEdgeType.VERIFICATION_REFER,
    }
)


class ValidationErrorCode(StrEnum):
    """Codes for workflow validation errors."""

    UNREACHABLE_NODE = "unreachable_node"
    END_NOT_REACHABLE = "end_not_reachable"
    CONDITIONAL_MISSING_TRUE = "conditional_missing_true"
    CONDITIONAL_MISSING_FALSE = "conditional_missing_false"
    CONDITIONAL_EXTRA_OUTGOING = "conditional_extra_outgoing"
    SPLIT_TOO_FEW_BRANCHES = "split_too_few_branches"
    TASK_MISSING_TITLE = "task_missing_title"
    CYCLE_DETECTED = "cycle_detected"
    SUBWORKFLOW_REF_MISSING = "subworkflow_ref_missing"
    SUBWORKFLOW_VERSION_UNPINNED = "subworkflow_version_unpinned"
    SUBWORKFLOW_NOT_FOUND = "subworkflow_not_found"
    SUBWORKFLOW_INPUT_MISSING = "subworkflow_input_missing"
    SUBWORKFLOW_INPUT_UNKNOWN = "subworkflow_input_unknown"
    SUBWORKFLOW_INPUT_TYPE_MISMATCH = "subworkflow_input_type_mismatch"
    SUBWORKFLOW_OUTPUT_MISSING = "subworkflow_output_missing"
    SUBWORKFLOW_OUTPUT_UNKNOWN = "subworkflow_output_unknown"
    SUBWORKFLOW_OUTPUT_TYPE_MISMATCH = "subworkflow_output_type_mismatch"
    SUBWORKFLOW_CYCLE_DETECTED = "subworkflow_cycle_detected"
    VERIFICATION_MISSING_PASS = "verification_missing_pass"  # noqa: S105
    VERIFICATION_MISSING_FAIL = "verification_missing_fail"
    VERIFICATION_MISSING_REFER = "verification_missing_refer"
    VERIFICATION_DUPLICATE_EDGE = "verification_duplicate_edge"
    VERIFICATION_EXTRA_OUTGOING = "verification_extra_outgoing"
    VERIFICATION_EDGE_OUTSIDE = "verification_edge_outside"
    VERIFICATION_MISSING_CONFIG = "verification_missing_config"


class WorkflowValidationError(BaseModel):
    """A single validation error with optional location context."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    code: ValidationErrorCode = Field(description="Error code")
    message: NotBlankStr = Field(description="Human-readable message")
    node_id: NotBlankStr | None = Field(
        default=None,
        description="Related node ID",
    )
    edge_id: NotBlankStr | None = Field(
        default=None,
        description="Related edge ID",
    )


class WorkflowValidationResult(BaseModel):
    """Result of validating a workflow definition."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    errors: tuple[WorkflowValidationError, ...] = Field(
        default=(),
        description="Validation errors",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def valid(self) -> bool:
        """Whether validation passed (no errors)."""
        return len(self.errors) == 0
