# module-kind: declarative
"""Domain models for the Layer 2 agent-session peer reviewer.

Frozen Pydantic v2 models with ``extra='forbid'`` for every input the
completion-oracle gate consumes or produces. The reviewer's own
:class:`CompletionOracleVerdict` IS the aggregate decision, so there is no
severity-rollup routing matrix here (unlike the red-team gate): findings
are evidence attached to the verdict, not the source of it.

The severity vocabulary is reused from the red-team subsystem
(:class:`RedTeamSeverity`) rather than duplicated. These models live in the
engine subsystem package because persistence imports the report / record
models for the durable verdict archive, and ``persistence -> engine`` is a
sanctioned import direction.
"""

from enum import StrEnum
from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.security.redteam.models import RedTeamSeverity, severity_rank

__all__ = [
    "MAX_ORACLE_FINDINGS_PER_REPORT",
    "MAX_ORACLE_SUMMARY_LENGTH",
    "CompletionOracleFinding",
    "CompletionOracleGateResult",
    "CompletionOracleReport",
    "CompletionOracleReportRecord",
    "CompletionOracleVerdict",
]


class CompletionOracleVerdict(StrEnum):
    """The independent reviewer's aggregate verdict for a deliverable.

    Members:
        APPROVE: The deliverable meets its acceptance criteria and (for a
            code task) builds and its tests pass; completion proceeds.
        APPROVE_WITH_NOTES: Approved, but the reviewer attached non-blocking
            observations; completion proceeds with the notes on the record.
        REJECT: The deliverable does not meet its criteria; routed back to
            IN_PROGRESS as rework with the reviewer's summary as the reason.
        ESCALATE: The reviewer could not reach a confident verdict (ambiguous
            criteria, insufficient evidence); parked for a human decision
            rather than silently passed.
    """

    APPROVE = "approve"
    APPROVE_WITH_NOTES = "approve_with_notes"
    REJECT = "reject"
    ESCALATE = "escalate"


_FINDING_EVIDENCE_REQUIRED_FROM: Final[RedTeamSeverity] = RedTeamSeverity.HIGH
"""Severity at and above which a finding must carry at least one evidence entry."""


class CompletionOracleFinding(BaseModel):
    """One observation the reviewer recorded against a deliverable.

    Attributes:
        criterion: The acceptance criterion this finding bears on, when it
            maps to one; ``None`` for a cross-cutting observation.
        severity: Severity tier (reused red-team vocabulary).
        description: Human-readable description of the issue.
        evidence: Direct quotes / references substantiating the finding.
            Required (non-empty) at :data:`_FINDING_EVIDENCE_REQUIRED_FROM`
            and above so the assignee can act on the rework.
        build_or_test_reference: The command or execution-record id the
            finding is grounded in, when it concerns the build / tests.
        suggested_fix: Optional remediation hint for the assignee.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    criterion: NotBlankStr | None = None
    severity: RedTeamSeverity
    description: NotBlankStr
    evidence: tuple[NotBlankStr, ...] = ()
    build_or_test_reference: NotBlankStr | None = None
    suggested_fix: NotBlankStr | None = None

    @model_validator(mode="after")
    def _require_evidence_when_blocking(self) -> Self:
        """High-severity findings must carry at least one evidence entry.

        Returns:
            The validated finding.

        Raises:
            ValueError: If a high-severity finding carries no evidence.
        """
        if (
            severity_rank(self.severity)
            >= severity_rank(_FINDING_EVIDENCE_REQUIRED_FROM)
            and not self.evidence
        ):
            msg = (
                f"CompletionOracleFinding with severity {self.severity.value!r} "
                "must carry at least one evidence entry."
            )
            raise ValueError(msg)
        return self


MAX_ORACLE_FINDINGS_PER_REPORT: Final[int] = 25
"""Upper bound on findings per report to keep critiques actionable."""

MAX_ORACLE_SUMMARY_LENGTH: Final[int] = 4096
"""Upper bound on the report summary; the reviewer (an LLM) controls its
length, so the bound caps archive-row size and the operator-facing rework
reason derived from it."""


def _forbid_self_review(
    reviewer_agent_id: str | None, executor_agent_id: str | None
) -> None:
    """Enforce that the reviewer is a distinct identity from the executor.

    The independence invariant, checked at the model layer. Its canonical
    twin is ``DecisionRecord._forbid_self_review`` in ``engine/decisions.py``
    and the ``decision_records`` row-level CHECK; the completion-oracle
    archive table carries the same CHECK, giving three enforcement layers.

    Guards the both-present case only. An absent reviewer means no review
    happened, which the verdict and summary already say; it is not a
    self-review, and refusing to record it would leave the gate unable to
    archive the very escalation that reports the gap.

    Raises:
        ValueError: If the reviewer and executor identities are equal.
    """
    if reviewer_agent_id is None or executor_agent_id is None:
        return
    if reviewer_agent_id == executor_agent_id:
        msg = (
            "Completion-oracle reviewer_agent_id must differ from "
            f"executor_agent_id (both {reviewer_agent_id!r}); an agent cannot "
            "independently review its own work."
        )
        raise ValueError(msg)


class CompletionOracleReport(BaseModel):
    """The structured verdict an independent reviewer files for a deliverable.

    Attributes:
        execution_id: The execution that produced the deliverable under
            review (the gate's key into the report repo).
        task_id: The deliverable's owning task.
        reviewer_agent_id: The independent reviewer's agent id, or ``None``
            on a report the gate synthesised because no reviewer ran. A
            filed report always names one; inventing an id for the other
            case would put a judge no operator could grant into the column
            verdict quality is compared by.
        executor_agent_id: The agent that produced the deliverable.
        verdict: The aggregate verdict.
        findings: Structured findings (may be empty on a clean approval).
            Bounded by :data:`MAX_ORACLE_FINDINGS_PER_REPORT`.
        summary: One-paragraph natural-language summary of the review.
        ran_build: Whether the reviewer built the deliverable.
        ran_tests: Whether the reviewer ran the deliverable's tests.
        test_command: The test command the reviewer ran, when it ran tests.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr
    task_id: NotBlankStr
    reviewer_agent_id: NotBlankStr | None = None
    executor_agent_id: NotBlankStr
    verdict: CompletionOracleVerdict
    findings: tuple[CompletionOracleFinding, ...] = ()
    summary: NotBlankStr = Field(max_length=MAX_ORACLE_SUMMARY_LENGTH)
    ran_build: bool = False
    ran_tests: bool = False
    test_command: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_report(self) -> Self:
        """Enforce distinct reviewer identity and the findings bound.

        Returns:
            The validated report.

        Raises:
            ValueError: If the reviewer is the executor, or the report
                carries more than ``MAX_ORACLE_FINDINGS_PER_REPORT`` findings.
        """
        _forbid_self_review(self.reviewer_agent_id, self.executor_agent_id)
        if len(self.findings) > MAX_ORACLE_FINDINGS_PER_REPORT:
            msg = (
                f"CompletionOracleReport carries {len(self.findings)} findings; "
                f"the maximum is {MAX_ORACLE_FINDINGS_PER_REPORT}."
            )
            raise ValueError(msg)
        if self.test_command is not None and not self.ran_tests:
            msg = (
                "CompletionOracleReport carries a test_command "
                f"{self.test_command!r} but ran_tests is False; a report cannot "
                "name a test command it did not run."
            )
            raise ValueError(msg)
        return self


class CompletionOracleGateResult(BaseModel):
    """What the peer-review gate returns to its caller.

    Attributes:
        verdict: The aggregate verdict.
        report: The reviewer's filed report.
        elapsed_seconds: Wall-clock gate duration (clock-driven, deterministic
            under ``FakeClock``).
        reviewer_unstaffed: Whether the ESCALATE happened because nobody in
            the org holds the reviewer role. The gate is the only thing that
            knows why it escalated, so it says rather than leaving a caller to
            infer it from a summary string. It decides how the park is
            answered: an ordinary escalation waits on a human, this waits on
            staffing and is re-judged once the role is held.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdict: CompletionOracleVerdict
    report: CompletionOracleReport
    elapsed_seconds: float = Field(ge=0.0)
    reviewer_unstaffed: bool = False

    @model_validator(mode="after")
    def _verdict_matches_report(self) -> Self:
        """Reject a result whose top-level verdict disagrees with the report.

        The redundant top-level ``verdict`` is a convenience for callers that
        read the result without unpacking the report; guarding the agreement
        (the twin of ``CompletionOracleReportRecord._keys_match_report``) means
        one caller reading ``result.verdict`` and another reading
        ``result.report.verdict`` can never disagree on whether review passed.

        Returns:
            The validated result.

        Raises:
            ValueError: If ``verdict`` and ``report.verdict`` differ.
        """
        if self.verdict is not self.report.verdict:
            msg = (
                f"CompletionOracleGateResult.verdict {self.verdict.value!r} does "
                f"not match report.verdict {self.report.verdict.value!r}."
            )
            raise ValueError(msg)
        return self


class CompletionOracleReportRecord(BaseModel):
    """Durable audit record of one peer-review gate evaluation.

    The persistent archive row for one review: the reviewer's verdict +
    report and the time the gate recorded it, so an operator can answer "why
    was this deliverable sent back?" from the flight-recorder surface long
    after the run. A row is one review EVENT, so an execution decided,
    re-opened and decided again archives twice.

    ``report.execution_id`` / ``report.task_id`` MUST match the record-level
    keys so the queryable columns never disagree with the embedded report.

    Attributes:
        report_id: The archive's own key for this row, assigned by the store.
            ``None`` on a record being written, since the store assigns it,
            and set on every record read back. It is what keeps two reviews
            of one execution apart, so it is also the tiebreaker the
            newest-first sort and its keyset cursor close on.
        execution_id: The execution the gate evaluated.
        task_id: The deliverable's owning task.
        verdict: The aggregate verdict.
        report: The reviewer's filed report.
        recorded_at: When the gate recorded the verdict (clock-driven, so it
            is deterministic under ``FakeClock``).
        reviewer_agent_id: Who reviewed, taken from the gate's own selection
            rather than the filed report, because a report is written by the
            thing under scrutiny. ``None`` when no reviewer ran, which is the
            honest record of an escalation reporting exactly that.
        executor_agent_id: Whose work it was.
        reviewer_provider: The connection the reviewer dispatched on.
        reviewer_model_id: The model it ran, recorded per review because an
            agent's current binding is not evidence of what ran months ago,
            and verdict quality is compared per agent AND per model.
        reviewer_capability: The tier that model was graded at.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    report_id: int | None = None
    execution_id: NotBlankStr
    task_id: NotBlankStr
    verdict: CompletionOracleVerdict
    report: CompletionOracleReport
    recorded_at: AwareDatetime
    reviewer_agent_id: NotBlankStr | None = None
    executor_agent_id: NotBlankStr | None = None
    reviewer_provider: NotBlankStr | None = None
    reviewer_model_id: NotBlankStr | None = None
    reviewer_capability: CapabilityLevel | None = None

    @model_validator(mode="after")
    def _keys_match_report(self) -> Self:
        """Reject a record whose keys disagree with the embedded report.

        Returns:
            The validated record.

        Raises:
            ValueError: If ``report.execution_id`` / ``report.task_id`` /
                ``report.verdict`` do not match the record-level values.
        """
        if self.report.execution_id != self.execution_id:
            msg = (
                f"CompletionOracleReportRecord.execution_id {self.execution_id!r} "
                f"does not match report.execution_id {self.report.execution_id!r}."
            )
            raise ValueError(msg)
        if self.report.task_id != self.task_id:
            msg = (
                f"CompletionOracleReportRecord.task_id {self.task_id!r} does not "
                f"match report.task_id {self.report.task_id!r}."
            )
            raise ValueError(msg)
        if self.report.verdict is not self.verdict:
            msg = (
                f"CompletionOracleReportRecord.verdict {self.verdict.value!r} does "
                f"not match report.verdict {self.report.verdict.value!r}."
            )
            raise ValueError(msg)
        _forbid_self_review(self.reviewer_agent_id, self.executor_agent_id)
        return self
