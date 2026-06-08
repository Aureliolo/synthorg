"""Vision verifier domain models.

Frozen Pydantic v2 models with ``extra='forbid'`` for every input the
vision gate consumes or produces. The UI cousin of the red-team models:
findings carry a severity that the routing matrix maps to a blocking
verdict under the deliverable's autonomy posture.

Self-evaluation is rejected the same way the verification grader rejects
it: ``evaluator_agent_id`` must differ from ``generator_agent_id``.
"""

from enum import StrEnum
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr

_RGB_MIN: Final[int] = 0
_RGB_MAX: Final[int] = 255
_SHA256_HEX_PATTERN: Final[str] = "^[a-f0-9]{64}$"
MAX_FINDINGS_PER_REPORT: Final[int] = 25


class VisionSeverity(StrEnum):
    """Severity of a single vision finding (ordered via ``_SEVERITY_RANK``)."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_RANK_INFO: Final[int] = 0
SEVERITY_RANK_LOW: Final[int] = 1
SEVERITY_RANK_MEDIUM: Final[int] = 2
SEVERITY_RANK_HIGH: Final[int] = 3
SEVERITY_RANK_CRITICAL: Final[int] = 4

_SEVERITY_RANK: Final[dict[VisionSeverity, int]] = {
    VisionSeverity.INFO: SEVERITY_RANK_INFO,
    VisionSeverity.LOW: SEVERITY_RANK_LOW,
    VisionSeverity.MEDIUM: SEVERITY_RANK_MEDIUM,
    VisionSeverity.HIGH: SEVERITY_RANK_HIGH,
    VisionSeverity.CRITICAL: SEVERITY_RANK_CRITICAL,
}

_EVIDENCE_REQUIRED_FROM: Final[VisionSeverity] = VisionSeverity.HIGH


def severity_rank(severity: VisionSeverity) -> int:
    """Return the integer rank of ``severity`` (INFO=0 ... CRITICAL=4)."""
    return _SEVERITY_RANK[severity]


class VisionFindingCategory(StrEnum):
    """The dimension along which a vision finding flags a defect.

    Members:
        REQUIREMENTS_MISMATCH: The running UI contradicts the brief
            (wrong colour, wrong label, wrong initial state).
        MISSING_ELEMENT: An element the brief requires is absent.
        VISUAL_DEFECT: Rendering / layout defect (overlap, clipping).
    """

    REQUIREMENTS_MISMATCH = "requirements_mismatch"
    MISSING_ELEMENT = "missing_element"
    VISUAL_DEFECT = "visual_defect"


class VisionVerdict(StrEnum):
    """Aggregate verdict the gate returns for a deliverable.

    Members:
        PASS: No findings; deliverable proceeds to COMPLETED.
        PASS_WITH_FINDINGS: Findings exist but none meet the blocking
            threshold under the current autonomy.
        BLOCK: At least one finding blocks; deliverable is routed back
            to IN_PROGRESS as rework.
    """

    PASS = "pass"  # noqa: S105
    PASS_WITH_FINDINGS = "pass_with_findings"  # noqa: S105
    BLOCK = "block"


class VisualExpectationKind(StrEnum):
    """Discriminator for a structured, machine-checkable expectation."""

    DOMINANT_COLOUR = "dominant_colour"


class VisualExpectation(BaseModel):
    """A structured, deterministic expectation the heuristic verifier checks.

    The brief's free text is decomposed into machine-checkable
    expectations so the heuristic variant can flag a mismatch without an
    LLM. Today only ``dominant_colour`` is supported (the mean RGB of
    the screenshot must be within ``tolerance`` of ``expected_rgb``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: VisualExpectationKind
    description: NotBlankStr = Field(
        description="Human-readable expectation (e.g. 'background should be blue').",
    )
    expected_rgb: tuple[int, int, int] = Field(
        description="Target colour as (r, g, b), each 0-255.",
    )
    tolerance: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Maximum normalised RGB distance (0 = exact, 1 = opposite) "
            "before the expectation is treated as violated."
        ),
    )
    severity: VisionSeverity = Field(
        default=VisionSeverity.HIGH,
        description="Severity assigned to a violation of this expectation.",
    )

    @model_validator(mode="after")
    def _validate_rgb_bounds(self) -> Self:
        """Reject channel values outside 0-255.

        Returns:
            The validated expectation.

        Raises:
            ValueError: If any ``expected_rgb`` channel is out of range.
        """
        for channel in self.expected_rgb:
            if channel < _RGB_MIN or channel > _RGB_MAX:
                msg = f"expected_rgb channels must be {_RGB_MIN}-{_RGB_MAX}"
                raise ValueError(msg)
        return self


class VisionFinding(BaseModel):
    """A single vision finding against a running UI deliverable."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    category: VisionFindingCategory
    severity: VisionSeverity
    description: NotBlankStr
    evidence: tuple[NotBlankStr, ...] = ()
    suggested_fix: NotBlankStr | None = None
    screenshot_path: NotBlankStr | None = None

    @model_validator(mode="after")
    def _require_evidence_when_blocking(self) -> Self:
        """High-severity findings must carry at least one evidence entry.

        Returns:
            The validated finding.

        Raises:
            ValueError: If a high-severity finding carries no evidence.
        """
        if (
            severity_rank(self.severity) >= severity_rank(_EVIDENCE_REQUIRED_FROM)
            and not self.evidence
        ):
            msg = (
                f"VisionFinding with severity {self.severity.value!r} must "
                "carry at least one evidence entry."
            )
            raise ValueError(msg)
        return self


class VisionScreenshotRef(BaseModel):
    """A reference to a screenshot the verifier inspects."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    workspace_path: NotBlankStr = Field(
        description="Screenshot path relative to the workspace root.",
    )
    sha256: str = Field(
        pattern=_SHA256_HEX_PATTERN,
        description="Lowercase hex SHA-256 (64 chars) of the PNG bytes.",
    )


class VisionVerificationReport(BaseModel):
    """The structured report a vision verifier produces for one deliverable.

    ``evaluator_agent_id`` must differ from ``generator_agent_id`` to
    enforce the self-evaluation rejection constraint.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr
    execution_id: NotBlankStr
    findings: tuple[VisionFinding, ...] = ()
    summary: NotBlankStr
    verifier_kind: NotBlankStr
    model_id: NotBlankStr | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    generator_agent_id: NotBlankStr
    evaluator_agent_id: NotBlankStr

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        """Bound findings and reject self-evaluation.

        Returns:
            The validated report.

        Raises:
            ValueError: If the findings exceed the per-report cap, or the
                evaluator is also the generator.
        """
        if len(self.findings) > MAX_FINDINGS_PER_REPORT:
            msg = (
                f"VisionVerificationReport carries {len(self.findings)} "
                f"findings; the maximum is {MAX_FINDINGS_PER_REPORT}."
            )
            raise ValueError(msg)
        if self.evaluator_agent_id == self.generator_agent_id:
            msg = (
                "Self-evaluation rejected: evaluator_agent_id must differ "
                "from generator_agent_id"
            )
            raise ValueError(msg)
        return self


class VisionReviewInput(BaseModel):
    """What the gate sees on entry: the running UI plus its brief.

    Attributes:
        task_id: The deliverable's owning task.
        execution_id: The execution that produced the deliverable.
        brief: Free-text acceptance brief (consumed by ``llm_vision``).
        acceptance_criteria: The brief's atomic criteria.
        screenshots: Captured screenshots of the running app.
        expectations: Structured, machine-checkable expectations
            (consumed by the deterministic ``heuristic`` verifier).
        generator_agent_id: The agent that produced the deliverable.
        evaluator_agent_id: The agent / verifier identity performing the
            review (must differ from the generator).
        autonomy: Effective autonomy governing severity-tiered routing.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr
    execution_id: NotBlankStr
    brief: NotBlankStr
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(min_length=1)
    screenshots: tuple[VisionScreenshotRef, ...] = Field(min_length=1)
    expectations: tuple[VisualExpectation, ...] = ()
    generator_agent_id: NotBlankStr
    evaluator_agent_id: NotBlankStr
    autonomy: AutonomyLevel

    @model_validator(mode="after")
    def _reject_self_evaluation(self) -> Self:
        """Reject a verifier that is the deliverable's own generator.

        Returns:
            The validated review input.

        Raises:
            ValueError: If ``evaluator_agent_id`` equals
                ``generator_agent_id``.
        """
        if self.evaluator_agent_id == self.generator_agent_id:
            msg = (
                "Self-evaluation rejected: evaluator_agent_id must differ "
                "from generator_agent_id"
            )
            raise ValueError(msg)
        return self


class VisionGateResult(BaseModel):
    """What the gate returns to its caller."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdict: VisionVerdict
    report: VisionVerificationReport
    elapsed_seconds: float = Field(ge=0.0)
