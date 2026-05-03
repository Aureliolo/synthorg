"""Health monitoring pipeline models.

Frozen Pydantic models for escalation tickets emitted by the
health judge and consumed by the triage filter.
"""

import copy
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.quality.models import StepQualitySignal  # noqa: TC001


class EscalationSeverity(StrEnum):
    """Severity level for health escalation tickets."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EscalationCause(StrEnum):
    """Root cause category for an escalation ticket."""

    STAGNATION = "stagnation"
    REPEATED_FAILURE = "repeated_failure"
    BUDGET_BREACH = "budget_breach"
    QUALITY_DEGRADATION = "quality_degradation"
    TIMEOUT = "timeout"


class EscalationTicket(BaseModel):
    """Structured health escalation ticket.

    Emitted by the ``HealthJudge`` (sensitive layer) and filtered
    by the ``TriageFilter`` (conservative layer) before delivery
    to the ``NotificationDispatcher``.

    Attributes:
        id: Unique ticket identifier.
        cause: Root cause category.
        severity: Escalation severity level.
        evidence: Human-readable description of the evidence.
        agent_id: Agent that triggered the escalation.
        task_id: Task being executed when the escalation occurred.
        steps_since_last_progress: Steps without forward progress.
        stall_duration_seconds: Wall-clock duration of the stall.
        quality_signals: Step-level quality signals (empty when
            quality classification is unavailable).
        created_at: When the ticket was created (UTC).
        metadata: Arbitrary structured context.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique ticket identifier",
    )
    cause: EscalationCause = Field(
        description="Root cause category",
    )
    severity: EscalationSeverity = Field(
        description="Escalation severity level",
    )
    evidence: NotBlankStr = Field(
        description="Human-readable evidence description",
    )
    agent_id: NotBlankStr = Field(
        description="Agent identifier",
    )
    task_id: NotBlankStr = Field(
        description="Task identifier",
    )
    steps_since_last_progress: int = Field(
        default=0,
        ge=0,
        description="Steps without forward progress",
    )
    stall_duration_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Wall-clock stall duration in seconds",
    )
    quality_signals: tuple[StepQualitySignal, ...] = Field(
        default=(),
        description="Step-level quality signals (empty when unavailable)",
    )
    created_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Ticket creation timestamp (UTC)",
    )
    metadata: Mapping[str, object] = Field(
        default_factory=lambda: MappingProxyType({}),
        description="Arbitrary structured context (read-only at runtime)",
    )

    def __init__(self, **data: object) -> None:
        """Deep-copy metadata dict and wrap as a read-only mapping.

        ``ConfigDict(frozen=True)`` only blocks attribute rebinding;
        without the ``MappingProxyType`` wrap a caller could still
        mutate ``ticket.metadata['key'] = value`` after construction
        and break the documented immutability contract per
        CLAUDE.md ``## Code Conventions`` (immutability covenant).
        """
        if "metadata" in data:
            raw = data["metadata"]
            if isinstance(raw, Mapping):
                # Always deep-copy before wrapping; reusing an
                # incoming ``MappingProxyType`` directly would alias
                # its backing dict and let the caller mutate
                # ``ticket.metadata`` after construction.
                data["metadata"] = MappingProxyType(copy.deepcopy(dict(raw)))
        super().__init__(**data)
