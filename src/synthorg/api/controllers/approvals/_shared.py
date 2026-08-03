# module-kind: code
"""Shared DTOs and fetch helpers for approvals.

Pure helper module consumed by both the approvals query and decision
controllers: the urgency-enriched response DTO + its conversion, and the
approval-store fetch-or-404 helper. No Litestar surface. The thresholds the
urgency bucketing reads come from :mod:`._urgency`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal, LiteralString, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg._core.features import require_service
from synthorg.api.controllers.approvals._urgency import (
    _URGENCY_CRITICAL_FALLBACK_SECONDS,
    _URGENCY_HIGH_FALLBACK_SECONDS,
)
from synthorg.api.responses import require_resource_or_404
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.artifact import ArtifactType
from synthorg.core.evidence import EvidencePackage
from synthorg.core.run_outcome import RunOutcome
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_RESOURCE_NOT_FOUND

logger = get_logger(__name__)


class UrgencyLevel(StrEnum):
    """How urgently a pending approval needs attention.

    Thresholds: ``critical`` < 1 hour, ``high`` < 4 hours,
    ``normal`` >= 4 hours, ``no_expiry`` when no TTL is set.
    """

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    NO_EXPIRY = "no_expiry"


class SafeEvidencePackageSignature(BaseModel):
    """Client-facing view of an approver signature without the raw bytes.

    The audit-chain signature material (``signature_bytes``) is a
    cryptographic secret: exposing it lets any API consumer replay or
    forge-check signatures offline. The wire view keeps the auditable
    metadata (who signed, with what algorithm, when, and the chain
    position) but omits the raw signature bytes entirely.

    Attributes:
        approver_id: Identity of the approver.
        algorithm: Signature algorithm used.
        signed_at: When the signature was produced.
        chain_position: Position in the append-only audit chain.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    approver_id: NotBlankStr = Field(description="Approver identity")
    algorithm: Literal["ml-dsa-65", "ed25519"] = Field(
        description="Signature algorithm",
    )
    signed_at: datetime = Field(description="Signature timestamp")
    chain_position: int = Field(
        ge=0,
        description="Position in the append-only audit chain",
    )


class SafeEvidencePackage(EvidencePackage):
    """Client-facing evidence package whose signatures omit the raw bytes.

    Narrows :class:`EvidencePackage.signatures` to the redacted
    :class:`SafeEvidencePackageSignature` element type so the raw
    ``signature_bytes`` can never reach an API response. The inherited
    ``is_fully_signed`` computed field still works (it counts distinct
    ``approver_id`` values, which the safe signature retains).
    """

    # Narrowing the element type to the redacted signature is the whole
    # point of this subclass; Pydantic supports the field override but
    # mypy treats the annotation as an invariant reassignment. The
    # narrowed type is strictly safer (a subset of the base fields), and
    # ``_to_safe_evidence`` only ever constructs it without bytes.
    signatures: tuple[SafeEvidencePackageSignature, ...] = Field(  # type: ignore[assignment]
        default=(),
        description="Collected approver signatures (raw bytes redacted)",
    )


def _to_safe_evidence(
    evidence: EvidencePackage | None,
) -> SafeEvidencePackage | None:
    """Redact signature bytes from an evidence package for the wire.

    Args:
        evidence: The domain evidence package (or ``None``).

    Returns:
        A :class:`SafeEvidencePackage` with each signature's raw bytes
        stripped, or ``None`` when there is no evidence.
    """
    if evidence is None:
        return None
    safe_signatures = tuple(
        SafeEvidencePackageSignature(
            approver_id=sig.approver_id,
            algorithm=sig.algorithm,
            signed_at=sig.signed_at,
            chain_position=sig.chain_position,
        )
        for sig in evidence.signatures
    )
    return SafeEvidencePackage(
        **evidence.model_dump(exclude={"signatures", "is_fully_signed"}),
        signatures=safe_signatures,
    )


class ApprovalTaskRef(BaseModel):
    """Resolved task identity for an approval (a name, not a UUID)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Task identifier")
    title: NotBlankStr = Field(description="Human-readable task title")
    status: TaskStatus = Field(description="Current task status")


class ApprovalProjectRef(BaseModel):
    """Resolved project identity for an approval."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Project identifier")
    name: NotBlankStr = Field(description="Human-readable project name")


class ApprovalAgentRef(BaseModel):
    """Resolved requesting-agent identity for an approval."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Agent identifier")
    name: NotBlankStr = Field(
        description="Agent display name (falls back to the id when unresolved)",
    )


class ApprovalArtifactRef(BaseModel):
    """A produced-artifact reference shown in the review surface."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Artifact identifier")
    path: NotBlankStr = Field(description="Artifact path")
    type: ArtifactType = Field(description="Artifact type")
    content_type: str = Field(default="", description="MIME content type")
    size_bytes: int = Field(default=0, ge=0, description="Content size in bytes")


class ApprovalRunSummary(BaseModel):
    """Outcome + produced artifacts for the run under review.

    Read-time derived (never persisted): the dashboard read model reuses
    this shape and :class:`RunOutcome` directly.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    outcome: RunOutcome = Field(description="Truthful run outcome")
    produced_artifact_count: int = Field(
        ge=0,
        description="Total artifacts the run produced (may exceed len(artifacts))",
    )
    artifacts: tuple[ApprovalArtifactRef, ...] = Field(
        default=(),
        description="Produced-artifact refs, capped for payload size",
    )

    @model_validator(mode="after")
    def _artifacts_within_count(self) -> Self:
        """The embedded refs are a (possibly capped) subset of the total.

        Returns:
            The validated model.

        Raises:
            ValueError: When more refs are embedded than the produced count.
        """
        if len(self.artifacts) > self.produced_artifact_count:
            msg = "artifacts cannot exceed produced_artifact_count"
            raise ValueError(msg)
        return self


class ApprovalContext(BaseModel):
    """Resolved enrichment bundle for one approval (read-time only).

    Every field is best-effort: a missing or unwired dependency leaves it
    ``None`` rather than failing the queue.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task: ApprovalTaskRef | None = None
    project: ApprovalProjectRef | None = None
    agent: ApprovalAgentRef | None = None
    run: ApprovalRunSummary | None = None

    @model_validator(mode="after")
    def _run_and_project_imply_task(self) -> Self:
        """A run summary or project ref only exists alongside a resolved task.

        Both are derived from the task in the producer, so their presence
        without a task would be an inconsistent bundle.

        Returns:
            The validated model.

        Raises:
            ValueError: When a run/project ref is present with no task.
        """
        if self.task is None and (self.run is not None or self.project is not None):
            msg = "run/project context requires a resolved task"
            raise ValueError(msg)
        return self


class ApprovalResponse(ApprovalItem):
    """Approval item enriched with urgency + resolved review context.

    Attributes:
        seconds_remaining: Seconds until expiry, clamped to 0.0 for
            expired items (``None`` if no TTL).
        urgency_level: Urgency classification based on time remaining.
        evidence_package: Structured evidence with signature bytes
            redacted (see :class:`SafeEvidencePackage`).
        task: Resolved task identity (title + status), ``None`` when
            unresolvable.
        project: Resolved project identity (name), ``None`` when
            unresolvable.
        agent: Resolved requesting-agent identity (display name).
        run: Run outcome + produced-artifact summary for the reviewer.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    evidence_package: SafeEvidencePackage | None = Field(
        default=None,
        description="Structured evidence for HITL approval (bytes redacted)",
    )
    seconds_remaining: float | None = Field(
        ge=0.0,
        description="Seconds until expiry (null if no TTL set)",
    )
    urgency_level: UrgencyLevel = Field(
        description="Urgency classification based on remaining time",
    )
    task: ApprovalTaskRef | None = Field(
        default=None,
        description="Resolved task identity (title + status)",
    )
    project: ApprovalProjectRef | None = Field(
        default=None,
        description="Resolved project identity (name)",
    )
    agent: ApprovalAgentRef | None = Field(
        default=None,
        description="Resolved requesting-agent identity (display name)",
    )
    run: ApprovalRunSummary | None = Field(
        default=None,
        description="Run outcome + produced-artifact summary",
    )


def _to_approval_response(
    item: ApprovalItem,
    *,
    now: datetime,
    urgency_critical_seconds: float,
    urgency_high_seconds: float,
    context: ApprovalContext | None = None,
) -> ApprovalResponse:
    """Convert an ApprovalItem to an ApprovalResponse with urgency + context.

    Args:
        item: The domain-layer approval item.
        now: Reference timestamp for computing seconds remaining.
        urgency_critical_seconds: Threshold below which urgency is
            ``CRITICAL`` (resolved per-request from the settings
            backend; falls back to the registry default).
        urgency_high_seconds: Threshold below which urgency is
            ``HIGH``.  Operators must satisfy
            ``urgency_critical_seconds < urgency_high_seconds``; the
            startup invariant validator
            (``lifecycle_helpers._validate_approval_urgency_invariant``)
            blocks bad combinations before traffic arrives.
        context: Resolved review context (task/project/agent/run). When
            ``None`` the response carries no enrichment fields.

    Returns:
        Response DTO with computed urgency and, when ``context`` is
        supplied, resolved task/project/agent/run fields.
    """
    ctx = context if context is not None else ApprovalContext()
    if item.expires_at is None:
        seconds_remaining = None
        urgency = UrgencyLevel.NO_EXPIRY
    else:
        seconds_remaining = max(0.0, (item.expires_at - now).total_seconds())
        # Inclusive comparisons: the settings contract is "at or below"
        # so a TTL exactly at the configured threshold is included in
        # the corresponding bucket (CRITICAL or HIGH) rather than spilling
        # into the next-laxer bucket.
        if seconds_remaining <= urgency_critical_seconds:
            urgency = UrgencyLevel.CRITICAL
        elif seconds_remaining <= urgency_high_seconds:
            urgency = UrgencyLevel.HIGH
        else:
            urgency = UrgencyLevel.NORMAL
    return ApprovalResponse(
        **item.model_dump(exclude={"evidence_package"}),
        evidence_package=_to_safe_evidence(item.evidence_package),
        seconds_remaining=seconds_remaining,
        urgency_level=urgency,
        task=ctx.task,
        project=ctx.project,
        agent=ctx.agent,
        run=ctx.run,
    )


def to_response_without_context(
    item: ApprovalItem, *, now: datetime
) -> ApprovalResponse:
    """Build an ApprovalResponse with urgency but no resolved review context.

    For sync publish paths (lazy expiry) that have no ``app_state`` to
    resolve names: the frontend still receives a valid approval shape to
    upsert (status change), just without the resolved task/project/agent
    names. Uses the registry fallback urgency thresholds.

    Args:
        item: The domain-layer approval item.
        now: Reference timestamp for computing seconds remaining.

    Returns:
        Response DTO with urgency fields and no enrichment context.
    """
    return _to_approval_response(
        item,
        now=now,
        urgency_critical_seconds=_URGENCY_CRITICAL_FALLBACK_SECONDS,
        urgency_high_seconds=_URGENCY_HIGH_FALLBACK_SECONDS,
        context=None,
    )


async def _get_approval_or_404(
    app_state: AppState,
    approval_id: str,
    *,
    missing_message: LiteralString | None = None,
) -> ApprovalItem:
    """Fetch an approval item or raise NotFoundError.

    Args:
        app_state: Application state containing the approval store.
        approval_id: Approval identifier.
        missing_message: Fixed 404 text replacing the default, which quotes
            the caller's identifier back. A door that also 404s an in-range
            id it refuses (the chat question surface) passes the same
            constant on both paths so the two are indistinguishable. The
            structured log still records the real identifier, so the two
            cases stay distinguishable server-side.

    Returns:
        The matching approval item.

    Raises:
        NotFoundError: If the approval is not found.
    """
    store = require_service(app_state.slice(ApprovalStateSlice).store, "Approval Store")
    return require_resource_or_404(
        await store.get(approval_id),
        resource_type="Approval",
        identifier=approval_id,
        log_event=API_RESOURCE_NOT_FOUND,
        operation="read",
        message=missing_message,
    )
