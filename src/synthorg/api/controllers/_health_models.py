# module-kind: declarative
"""Response payloads for the liveness, readiness and health endpoints."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.controllers._backup_health import BackupHealth
from synthorg.api.controllers._cost_recording_health import CostRecordingHealth
from synthorg.api.controllers._health_probes import TelemetryStatus
from synthorg.api.controllers._memory_health import MemoryHealth
from synthorg.providers.health import ProviderReachability


class ReadinessOutcome(StrEnum):
    """Binary readiness outcome.

    Readiness is a pass/fail gate for supervisors, so it stays binary: a
    supervisor has no sensible action attached to a tri-state ``degraded``
    outcome, which leaves it deciding between restarting a process that is
    serving and ignoring one that is not.
    """

    OK = "ok"
    UNAVAILABLE = "unavailable"


class LivenessStatus(BaseModel):
    """Liveness response payload.

    Carries no version, for the same reason :class:`ReadinessProbe` does not.

    Attributes:
        status: Always ``"ok"``.
        uptime_seconds: Seconds since startup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status: Literal["ok"] = Field(
        default="ok",
        description="Always 'ok' while the process is alive",
    )
    uptime_seconds: float = Field(ge=0.0, description="Seconds since startup")


class ReadinessProbe(BaseModel):
    """Minimal readiness payload for the unauthenticated ``/readyz`` probe.

    Deliberately carries no component topology (persistence / message
    bus / provider / telemetry state): the binary outcome plus uptime is all a
    supervisor or load-balancer needs, and exposing the component breakdown to
    unauthenticated callers aids reconnaissance. The authenticated ``/health``
    endpoint returns the full :class:`ReadinessStatus` breakdown for operators.

    The exact build version is withheld on the same grounds: it tells an
    unauthenticated caller precisely which published advisories apply, and no
    supervisor decision depends on it. An operator reads the running version
    from the authenticated breakdown, or locally from the deployed image tag.

    Attributes:
        status: Overall readiness outcome.
        uptime_seconds: Seconds since startup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status: ReadinessOutcome = Field(description="Overall readiness outcome")
    uptime_seconds: float = Field(ge=0.0, description="Seconds since startup")


class ReadinessStatus(BaseModel):
    """Readiness response payload.

    Attributes:
        status: Overall readiness outcome.
        persistence: Persistence backend healthy (``None`` if not
            configured).
        message_bus: Message bus running (``None`` if not configured).
        providers: The worst verdict across every tracked LLM provider:
            ``ok``, ``degraded``, ``down``, or ``unknown`` when the read
            itself failed. More than a boolean, which has to fold
            ``DEGRADED`` into one side or the other and so reports a
            provider failing some calls identically to one failing none, or
            else identically to one that is down. ``None`` when no provider
            health tracker is wired (dev stacks without provider config).
            Excluded from the ``status`` roll-up on the same grounds as
            ``backup``: every replica reaches the same third-party
            endpoint, so gating readiness on it would drain them all at
            once and take down the dashboard an operator repoints a
            broken provider from.
        persistence_backend: Which backend is actually CONNECTED, read off
            the assembled backend rather than the config, because the two
            can differ and the operator needs to know which one is serving.
            The dashboard card named SQLite unconditionally, so a Postgres
            deployment was told the wrong backend and could not learn the
            right one from the product at all.
        telemetry: Project telemetry delivery state.
        memory: Agent-memory substrate state.
        backup: Backup coverage for this boot, and the cause when there is
            none. Deliberately excluded from the ``status`` roll-up: a
            process with no backup coverage still serves traffic correctly,
            so flipping readiness would have a supervisor restart a healthy
            deployment into the same condition.
        cost_recording: Whether LLM spend is currently being recorded.
        version: Application version.
        uptime_seconds: Seconds since startup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status: ReadinessOutcome = Field(description="Overall readiness outcome")
    persistence: bool | None = Field(
        description="Persistence backend healthy (None if not configured)",
    )
    persistence_backend: str | None = Field(
        default=None,
        description="Name of the connected persistence backend",
    )
    message_bus: bool | None = Field(
        description="Message bus running (None if not configured)",
    )
    providers: ProviderReachability | None = Field(
        default=None,
        description="Worst provider verdict: ok/degraded/down (None if unwired)",
    )
    telemetry: TelemetryStatus = Field(
        description="Project telemetry delivery state",
    )
    memory: MemoryHealth = Field(
        description="Agent-memory substrate state",
    )
    backup: BackupHealth = Field(
        description="Backup coverage for this boot",
    )
    cost_recording: CostRecordingHealth = Field(
        description="Whether LLM spend is currently being recorded",
    )
    version: str = Field(description="Application version")
    uptime_seconds: float = Field(ge=0.0, description="Seconds since startup")


__all__ = [
    "LivenessStatus",
    "ReadinessOutcome",
    "ReadinessProbe",
    "ReadinessStatus",
]
