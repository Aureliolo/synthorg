"""Adversarial red-team domain models.

Frozen Pydantic v2 models with ``extra='forbid'`` for every input the
red-team gate consumes or produces. Severity ordering is implemented
via the ``_SEVERITY_RANK`` lookup table so the enum stays a plain
``StrEnum`` while still supporting comparisons in
:mod:`synthorg.security.redteam.routing`.
"""

from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import AutonomyLevel  # noqa: TC001 -- Pydantic field type
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.security.redteam.grounding.models import UngroundedClaim  # noqa: TC001


class RedTeamAttackSurface(StrEnum):
    """The dimension along which the red-team attacked a deliverable.

    Members:
        CORRECTNESS: Does the deliverable do what was asked.
        SECURITY: Input validation, secret handling, injection sinks,
            authn/authz, OWASP-style defects.
        REQUIREMENTS: Mismatch between brief / acceptance criteria
            and the deliverable's actual content.
        GROUNDING: Claims asserted without traceable sources;
            hallucinated or ungrounded factual statements.
    """

    CORRECTNESS = "correctness"
    SECURITY = "security"
    REQUIREMENTS = "requirements"
    GROUNDING = "grounding"


class RedTeamSeverity(StrEnum):
    """Severity of a single red-team finding.

    Ordering is via :data:`_SEVERITY_RANK`. ``INFO`` is the floor
    (purely advisory); ``CRITICAL`` is the ceiling (always blocks
    completion regardless of autonomy).
    """

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

_SEVERITY_RANK: Final[dict[RedTeamSeverity, int]] = {
    RedTeamSeverity.INFO: SEVERITY_RANK_INFO,
    RedTeamSeverity.LOW: SEVERITY_RANK_LOW,
    RedTeamSeverity.MEDIUM: SEVERITY_RANK_MEDIUM,
    RedTeamSeverity.HIGH: SEVERITY_RANK_HIGH,
    RedTeamSeverity.CRITICAL: SEVERITY_RANK_CRITICAL,
}


def severity_rank(severity: RedTeamSeverity) -> int:
    """Return the integer rank of ``severity`` (INFO=0 ... CRITICAL=4)."""
    return _SEVERITY_RANK[severity]


class RedTeamVerdict(StrEnum):
    """Aggregate verdict the gate returns for a deliverable.

    Members:
        PASS: No findings; deliverable proceeds to COMPLETED.
        PASS_WITH_FINDINGS: Findings exist but none meet the blocking
            severity threshold under the current autonomy; deliverable
            proceeds to COMPLETED with findings attached to the
            audit record.
        BLOCK: At least one finding meets the blocking threshold;
            deliverable is routed back to IN_PROGRESS as rework.
    """

    PASS = "pass"  # noqa: S105
    PASS_WITH_FINDINGS = "pass_with_findings"  # noqa: S105
    BLOCK = "block"


_FINDING_EVIDENCE_REQUIRED_FROM: Final[RedTeamSeverity] = RedTeamSeverity.HIGH
"""Severity at and above which a finding must carry at least one evidence entry."""


class RedTeamFinding(BaseModel):
    """A single adversarial finding against a deliverable.

    Attributes:
        attack_surface: The dimension of attack
            (correctness / security / requirements / grounding).
        severity: Severity tier.
        description: Human-readable description of the defect.
        evidence: Direct quotes or references from the deliverable
            that substantiate the finding. Required (non-empty)
            for severity at or above :data:`_FINDING_EVIDENCE_REQUIRED_FROM`.
        suggested_fix: Optional remediation hint passed back to the
            assignee as part of the rework critique.
        source: Where the finding originated. ``"agent"`` for findings
            filed by the red-team agent via the tool; ``"heuristic"``
            for findings produced by the grounding stub;
            ``"knowledge_substrate"`` reserved for the
            substrate-backed checker.
        citations: Source references the finding cites
            (URLs, document IDs, etc.). Default empty.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    attack_surface: RedTeamAttackSurface
    severity: RedTeamSeverity
    description: NotBlankStr
    evidence: tuple[NotBlankStr, ...] = ()
    suggested_fix: NotBlankStr | None = None
    source: Literal["agent", "heuristic", "knowledge_substrate"] = "agent"
    citations: tuple[NotBlankStr, ...] = ()

    @model_validator(mode="after")
    def _require_evidence_when_blocking(self) -> Self:
        """High-severity findings must carry at least one evidence entry.

        Without evidence the assignee cannot act on the rework. Lower
        severities (INFO / LOW / MEDIUM) are advisory and may lack
        direct quotes (e.g. structural observations).
        """
        if (
            severity_rank(self.severity)
            >= severity_rank(_FINDING_EVIDENCE_REQUIRED_FROM)
            and not self.evidence
        ):
            msg = (
                f"RedTeamFinding with severity {self.severity.value!r} must "
                "carry at least one evidence entry."
            )
            raise ValueError(msg)
        return self


MAX_FINDINGS_PER_REPORT: Final[int] = 25
"""Upper bound on findings per report to keep critiques actionable."""


class RedTeamReport(BaseModel):
    """The structured report a red-team agent files for one deliverable.

    Attributes:
        execution_id: The execution that produced the deliverable
            under review (the gate's key into the report repo).
        task_id: The deliverable's owning task.
        findings: Structured findings tuple (may be empty for a
            clean deliverable). Bounded by :data:`MAX_FINDINGS_PER_REPORT`.
        summary: One-paragraph natural-language summary of the
            adversarial assessment. Required even when findings is
            empty so the audit record always carries a rationale.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr
    task_id: NotBlankStr
    findings: tuple[RedTeamFinding, ...] = ()
    summary: NotBlankStr

    @model_validator(mode="after")
    def _check_findings_bounded(self) -> Self:
        if len(self.findings) > MAX_FINDINGS_PER_REPORT:
            msg = (
                f"RedTeamReport carries {len(self.findings)} findings; "
                f"the maximum is {MAX_FINDINGS_PER_REPORT}."
            )
            raise ValueError(msg)
        return self


class RedTeamReviewInput(BaseModel):
    """What the gate sees on entry: the deliverable plus its context.

    The gate's evaluation surface. Lives here (not on Task directly) so
    the red-team subsystem can be exercised without dragging the full
    Task model and its dependencies into every test.

    Attributes:
        task_id: The deliverable's owning task.
        execution_id: The execution that produced the deliverable.
        deliverable_content: The artifact text the red-team attacks.
        acceptance_criteria: The brief's acceptance criteria, used by
            the agent prompt and by a future substrate-backed checker
            to verify requirements coverage.
        assigned_agent_id: The agent that produced the deliverable
            (forbidden as red-team reviewer; enforced one layer up by
            the review-gate's self-review guard).
        autonomy: Effective autonomy level governing severity-tiered
            routing in :mod:`synthorg.security.redteam.routing`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr
    execution_id: NotBlankStr
    deliverable_content: NotBlankStr
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(min_length=1)
    assigned_agent_id: NotBlankStr
    autonomy: AutonomyLevel


class RedTeamGateResult(BaseModel):
    """What the gate returns to its caller.

    Attributes:
        verdict: Aggregate verdict (PASS / PASS_WITH_FINDINGS / BLOCK).
        report: The agent-filed report, merged with heuristic
            grounding findings (added as ``source="heuristic"`` finding
            entries so callers see a single unified findings tuple).
        grounding_claims: Raw heuristic grounding claims, exposed for
            callers that want to surface them separately from the
            merged findings (e.g. for diagnostic UI).
        elapsed_seconds: Wall-clock gate duration (clock-driven, deterministic
            under ``FakeClock``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdict: RedTeamVerdict
    report: RedTeamReport
    grounding_claims: tuple[UngroundedClaim, ...] = ()
    elapsed_seconds: float = Field(ge=0.0)
