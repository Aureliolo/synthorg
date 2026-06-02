# module-kind: declarative
"""Provenance-receipt domain models.

A :class:`DeliverableReceipt` is the immutable, self-validating evidence
bundle attached to a completed deliverable. It aggregates six signal
groups (sources, decisions, cost, tests, red-team, cassette) into one
frozen record. Each entry stores enough to be re-checked after the fact:
sources carry a ``source_id`` + ``content_hash`` that must still resolve,
decisions carry ``entry_id`` + ``revision``, tests carry the persisted
``record_id``, the cassette carries a content hash of its file bytes.

The receipt is the system of record (persisted as JSON in the
``deliverable_receipt`` table); a human-readable projection is rendered
into the deliverable's living document separately.
"""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.currency import CurrencyCode
from synthorg.core.types import NotBlankStr
from synthorg.security.redteam.models import (
    RedTeamFinding,
    RedTeamSeverity,
    RedTeamVerdict,
    severity_rank,
)

__all__ = [
    "BLOCKING_SEVERITY_FLOOR",
    "DeliverableReceipt",
    "ReceiptCassetteRef",
    "ReceiptDecisionEntry",
    "ReceiptRedTeamEntry",
    "ReceiptSourceEntry",
    "ReceiptTestEntry",
    "ReceiptValidationResult",
]

#: Severity at and above which a red-team finding counts toward the
#: receipt's ``high_plus_count`` (HIGH and CRITICAL).
BLOCKING_SEVERITY_FLOOR: RedTeamSeverity = RedTeamSeverity.HIGH


class ReceiptSourceEntry(BaseModel):
    """One distinct knowledge source consulted during the run.

    ``source_id`` + ``content_hash`` are the resolvability handle: the
    validator re-fetches the source and confirms the hash still matches.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_id: NotBlankStr = Field(description="Stable knowledge source identifier")
    chunk_id: NotBlankStr = Field(description="Retrieved chunk identifier")
    title: NotBlankStr = Field(description="Source title at capture time")
    uri: NotBlankStr = Field(description="Source URI")
    content_hash: NotBlankStr = Field(
        description="SHA-256 of source content at capture",
    )


class ReceiptDecisionEntry(BaseModel):
    """One key decision (with rationale) recorded in the project brain."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry_id: NotBlankStr = Field(description="Stable brain-entry identifier")
    revision: int = Field(ge=1, description="Brain-entry revision captured")
    title: NotBlankStr = Field(description="Decision title")
    rationale: NotBlankStr = Field(description="Why the decision was taken")
    recorded_at: AwareDatetime = Field(description="When the decision was recorded")


class ReceiptTestEntry(BaseModel):
    """One test run executed during the deliverable's production.

    Carries only the audit signal (command, exit code, pass/timeout): the
    raw stdout/stderr tails stay on the internal ``code_execution_record``
    and are deliberately NOT surfaced here, since the receipt is returned
    over REST and rendered in the dashboard where sandbox output could leak
    secrets.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    record_id: NotBlankStr = Field(description="Persisted code-execution record id")
    command: NotBlankStr = Field(description="Test command that was run")
    returncode: int = Field(description="Process exit code")
    passed: bool = Field(description="True iff returncode 0 and not timed out")
    timed_out: bool = Field(description="Whether the run hit its time limit")
    executed_at: AwareDatetime = Field(description="When the run finished")

    @model_validator(mode="after")
    def _passed_is_consistent(self) -> Self:
        """A passing run must have exit code 0 and must not have timed out.

        Returns:
            The validated entry.

        Raises:
            ValueError: If ``passed`` is True while the run had a
                non-zero exit code or timed out.
        """
        if self.passed and (self.returncode != 0 or self.timed_out):
            msg = "passed=True requires returncode==0 and timed_out=False"
            raise ValueError(msg)
        return self


class ReceiptRedTeamEntry(BaseModel):
    """Snapshot of the adversarial-review outcome for the deliverable.

    Snapshotted at build time because the red-team report repository is
    process-local and single-shot; the receipt is the durable record.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    verdict: RedTeamVerdict = Field(description="Aggregate gate verdict")
    finding_count: int = Field(ge=0, description="Total findings captured")
    high_plus_count: int = Field(
        ge=0,
        description="Findings at or above HIGH severity",
    )
    summary: NotBlankStr = Field(description="Red-team summary")
    findings_snapshot: tuple[RedTeamFinding, ...] = Field(
        description="Full findings snapshot",
    )

    @model_validator(mode="after")
    def _counts_match_snapshot(self) -> Self:
        """Counts must agree with the snapshot they summarise.

        Returns:
            The validated entry.

        Raises:
            ValueError: If ``finding_count`` or ``high_plus_count`` does
                not match the supplied ``findings_snapshot``.
        """
        if self.finding_count != len(self.findings_snapshot):
            msg = "finding_count must equal len(findings_snapshot)"
            raise ValueError(msg)
        floor = severity_rank(BLOCKING_SEVERITY_FLOOR)
        high_plus = sum(
            1
            for finding in self.findings_snapshot
            if severity_rank(finding.severity) >= floor
        )
        if self.high_plus_count != high_plus:
            msg = "high_plus_count must equal count of findings at/above HIGH"
            raise ValueError(msg)
        return self


class ReceiptCassetteRef(BaseModel):
    """Reference to the replayable provider cassette for the run.

    Identity is the filesystem path (cassettes have no DB row); the
    ``content_hash`` lets the validator detect drift between issue time
    and replay time.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    path: NotBlankStr = Field(description="Cassette file path")
    content_hash: NotBlankStr = Field(description="SHA-256 of cassette bytes at issue")


class DeliverableReceipt(BaseModel):
    """Immutable provenance bundle for one completed deliverable."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    receipt_id: NotBlankStr = Field(description="Surrogate receipt identifier")
    task_id: NotBlankStr = Field(description="Task that produced the deliverable")
    project_id: NotBlankStr = Field(description="Owning project")
    execution_id: NotBlankStr = Field(description="Run identifier the receipt covers")
    deliverable_doc_slug: NotBlankStr = Field(
        description="Slug of the deliverable living document",
    )
    issued_at: AwareDatetime = Field(description="When the receipt was issued")
    total_cost: float = Field(ge=0, description="Aggregate run cost for the task")
    currency: CurrencyCode = Field(description="Currency of total_cost")
    sources: tuple[ReceiptSourceEntry, ...] = Field(
        default=(),
        description="Distinct knowledge sources consulted",
    )
    decisions: tuple[ReceiptDecisionEntry, ...] = Field(
        default=(),
        description="Key decisions and rationale",
    )
    tests: tuple[ReceiptTestEntry, ...] = Field(
        default=(),
        description="Test runs and results",
    )
    red_team: ReceiptRedTeamEntry | None = Field(
        default=None,
        description="Adversarial-review snapshot, when one ran",
    )
    cassette: ReceiptCassetteRef | None = Field(
        default=None,
        description="Replayable cassette reference, when recording was active",
    )


class ReceiptValidationResult(BaseModel):
    """Outcome of validating a receipt for consistency.

    ``valid`` is ``True`` when every present signal is consistent:
    sources resolve with a matching content hash, the cassette (if any)
    loads and hashes to its recorded digest, and claimed test results
    reconcile against the persisted records. Absent signals are allowed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    valid: bool = Field(description="Whether all present signals are consistent")
    errors: tuple[str, ...] = Field(
        default=(),
        description="Human-readable inconsistencies; empty when valid",
    )
