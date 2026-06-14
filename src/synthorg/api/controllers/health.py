"""Liveness and readiness probe controllers.

* ``/healthz`` (liveness): always 200 while the event loop is
  turning; no dependency probes. Kubernetes-style supervisors use
  this to decide whether to restart the process.
* ``/readyz`` (readiness): 200 only when persistence + message
  bus are both healthy; otherwise 503. Used to gate traffic / block
  rollouts until dependencies are up.
"""

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Literal

from litestar import Controller, Response, get
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field

from synthorg import __version__
from synthorg._core.features import require_service
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.state import AppState
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_HEALTH_CHECK
from synthorg.persistence.state import PersistenceStateSlice, persistence_of
from synthorg.providers.state import ProvidersStateSlice
from synthorg.telemetry.state import TelemetryStateSlice

logger = get_logger(__name__)


class ReadinessOutcome(StrEnum):
    """Binary readiness outcome.

    Readiness is a pass/fail gate for supervisors; we deliberately
    drop the tri-state ``degraded`` value that the old ``/health``
    endpoint used -- a supervisor has no sensible action for it.
    """

    OK = "ok"
    UNAVAILABLE = "unavailable"


class TelemetryStatus(StrEnum):
    """Project telemetry runtime state.

    ``enabled`` means the collector is opted in AND the reporter can
    deliver events. ``disabled`` covers every other case.
    """

    ENABLED = "enabled"
    DISABLED = "disabled"


class LivenessStatus(BaseModel):
    """Liveness response payload.

    Attributes:
        status: Always ``"ok"``.
        version: Application version.
        uptime_seconds: Seconds since startup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status: Literal["ok"] = Field(
        default="ok",
        description="Always 'ok' while the process is alive",
    )
    version: str = Field(description="Application version")
    uptime_seconds: float = Field(ge=0.0, description="Seconds since startup")


class ReadinessProbe(BaseModel):
    """Minimal readiness payload for the unauthenticated ``/readyz`` probe.

    Deliberately carries no component topology (persistence / message
    bus / provider / telemetry state): the binary outcome plus version
    and uptime is all a supervisor or load-balancer needs, and exposing
    the component breakdown to unauthenticated callers aids
    reconnaissance. The authenticated ``/health`` endpoint returns the
    full :class:`ReadinessStatus` breakdown for operators.

    Attributes:
        status: Overall readiness outcome.
        version: Application version.
        uptime_seconds: Seconds since startup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status: ReadinessOutcome = Field(description="Overall readiness outcome")
    version: str = Field(description="Application version")
    uptime_seconds: float = Field(ge=0.0, description="Seconds since startup")


class ReadinessStatus(BaseModel):
    """Readiness response payload.

    Attributes:
        status: Overall readiness outcome.
        persistence: Persistence backend healthy (``None`` if not
            configured).
        message_bus: Message bus running (``None`` if not configured).
        providers: All tracked LLM providers reachable (no ``DOWN``
            status). ``None`` when no provider health tracker is
            wired (dev stacks without provider configuration).
        telemetry: Project telemetry delivery state.
        version: Application version.
        uptime_seconds: Seconds since startup.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    status: ReadinessOutcome = Field(description="Overall readiness outcome")
    persistence: bool | None = Field(
        description="Persistence backend healthy (None if not configured)",
    )
    message_bus: bool | None = Field(
        description="Message bus running (None if not configured)",
    )
    providers: bool | None = Field(
        default=None,
        description="All tracked providers reachable (None if not configured)",
    )
    telemetry: TelemetryStatus = Field(
        description="Project telemetry delivery state",
    )
    version: str = Field(description="Application version")
    uptime_seconds: float = Field(ge=0.0, description="Seconds since startup")


async def _probe_service(
    *,
    configured: bool,
    probe: Callable[[], Awaitable[bool]],
    component: str,
) -> bool | None:
    """Probe an async service, returning None if not configured.

    Returns:
        The ``bool`` value when present, ``None`` otherwise.
    """
    if not configured:
        return None
    try:
        return await probe()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # ``exc_info=True`` would serialize frame locals from the probe
        # into the log record; persistence / bus probes carry connection
        # objects and partial auth state, so we emit only the sanitized
        # description (see CLAUDE.md ``## Logging``).
        logger.warning(
            API_HEALTH_CHECK,
            component=component,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False


def _resolve_telemetry_status(app_state: AppState) -> TelemetryStatus:
    """Read the telemetry collector and map to a public status.

    Returns:
        ``TelemetryStatus`` instance.
    """
    collector = app_state.slice(TelemetryStateSlice).collector
    if collector is None:
        return TelemetryStatus.DISABLED
    return (
        TelemetryStatus.ENABLED if collector.is_functional else TelemetryStatus.DISABLED
    )


def _unavailable_status(app_state: AppState) -> ReadinessStatus:
    """Build a 503 ``unavailable`` readiness status.

    Used when the probe TaskGroup itself raises an unexpected error;
    we still want to emit a well-formed envelope so operator tooling
    can parse it, rather than letting a 500 surface.

    Returns:
        ``ReadinessStatus`` instance with every component unknown.
    """
    uptime = round(app_state.clock.monotonic() - app_state.startup_time, 2)
    return ReadinessStatus(
        status=ReadinessOutcome.UNAVAILABLE,
        persistence=None,
        message_bus=None,
        providers=None,
        telemetry=_resolve_telemetry_status(app_state),
        version=__version__,
        uptime_seconds=uptime,
    )


def _status_code_for(outcome: ReadinessOutcome) -> int:
    """Map a readiness outcome to its HTTP status code.

    Returns:
        ``200`` when ready, ``503`` otherwise.
    """
    return 200 if outcome is ReadinessOutcome.OK else 503


async def _evaluate_readiness(app_state: AppState) -> ReadinessStatus:
    """Probe every configured dependency and compute the readiness status.

    Shared by the unauthenticated ``/readyz`` probe (which projects this
    onto a topology-free :class:`ReadinessProbe`) and the authenticated
    ``/health`` detail endpoint (which returns it verbatim).

    Returns:
        The full :class:`ReadinessStatus` (``unavailable`` with unknown
        components if the probe TaskGroup raises a non-fatal error).

    Raises:
        BaseExceptionGroup: Re-raised only for fatal signals
            (MemoryError / RecursionError / CancelledError).
    """
    try:
        async with asyncio.TaskGroup() as tg:
            persistence_task = tg.create_task(
                _probe_service(
                    configured=app_state.slice(PersistenceStateSlice).backend
                    is not None,
                    probe=lambda: persistence_of(app_state).health_check(),
                    component="persistence",
                ),
            )
            bus_task = tg.create_task(
                _probe_service(
                    configured=app_state.slice(CommunicationStateSlice).message_bus
                    is not None,
                    probe=lambda: require_service(
                        app_state.slice(CommunicationStateSlice).message_bus,
                        "Message Bus",
                    ).health_check(),
                    component="message_bus",
                ),
            )

            async def _probe_providers() -> bool:
                return await require_service(
                    app_state.slice(ProvidersStateSlice).health_tracker,
                    "Provider Health Tracker",
                ).are_all_reachable()

            providers_task = tg.create_task(
                _probe_service(
                    configured=app_state.slice(ProvidersStateSlice).health_tracker
                    is not None,
                    probe=_probe_providers,
                    component="providers",
                ),
            )
    except BaseExceptionGroup as group:
        # Preserve fatal signals (MemoryError / RecursionError /
        # CancelledError) so the process supervisor still sees them;
        # everything else downgrades to an ``unavailable`` readiness.
        fatal = [
            exc
            for exc in group.exceptions
            if isinstance(exc, MemoryError | RecursionError | asyncio.CancelledError)
        ]
        if fatal:
            raise
        log_exception_redacted(logger, API_HEALTH_CHECK, group, component="readiness")
        return _unavailable_status(app_state)

    persistence_ok = persistence_task.result()
    bus_ok = bus_task.result()
    providers_ok = providers_task.result()
    telemetry_status = _resolve_telemetry_status(app_state)

    # Readiness is a pass/fail: every *configured* dependency must
    # report healthy. Unconfigured (None) is treated as not blocking
    # -- dev stacks without a bus still report ready.
    configured_checks = [
        v for v in (persistence_ok, bus_ok, providers_ok) if v is not None
    ]
    ready = bool(configured_checks) and all(configured_checks)
    outcome = (
        ReadinessOutcome.OK
        if ready or not configured_checks
        else ReadinessOutcome.UNAVAILABLE
    )
    uptime = round(app_state.clock.monotonic() - app_state.startup_time, 2)
    logger.debug(
        API_HEALTH_CHECK,
        status=outcome.value,
        persistence=persistence_ok,
        message_bus=bus_ok,
        providers=providers_ok,
        telemetry=telemetry_status.value,
    )
    return ReadinessStatus(
        status=outcome,
        persistence=persistence_ok,
        message_bus=bus_ok,
        providers=providers_ok,
        telemetry=telemetry_status,
        version=__version__,
        uptime_seconds=uptime,
    )


class LivenessController(Controller):
    """Liveness probe endpoint.

    Kubernetes-style supervisors hit ``/healthz`` to decide whether
    to restart the process. No dependency probes -- only that the
    event loop is responsive.
    """

    path = "/healthz"
    tags = ("health",)

    @get()
    async def liveness(
        self,
        state: State,
    ) -> ApiResponse[LivenessStatus]:
        """Return a constant ``ok`` response while the process is alive.

        Returns:
            ``ApiResponse[LivenessStatus]`` instance.
        """
        app_state: AppState = state.app_state
        uptime = round(app_state.clock.monotonic() - app_state.startup_time, 2)
        return ApiResponse(
            data=LivenessStatus(
                status="ok",
                version=__version__,
                uptime_seconds=uptime,
            ),
        )


class ReadinessController(Controller):
    """Readiness probe endpoint.

    Intentionally unauthenticated (excluded from the auth middleware in
    ``middleware_factory``): supervisors and load-balancers must reach
    it without credentials to gate traffic. For that reason the body is
    deliberately topology-free, carrying only the binary ``ok`` /
    ``unavailable`` outcome plus version and uptime; the per-component
    breakdown (persistence / message bus / providers / telemetry) lives
    behind authentication on ``GET /health`` (:class:`HealthController`).
    Returns 200 when every configured dependency is healthy, else 503.
    """

    path = "/readyz"
    tags = ("health",)

    @get()
    async def readiness(
        self,
        state: State,
    ) -> Response[ApiResponse[ReadinessProbe]]:
        """Return a topology-free readiness outcome + 200/503.

        Returns:
            ``Response[ApiResponse[ReadinessProbe]]`` instance.

        Raises:
            BaseExceptionGroup: Re-raised only for fatal signals
                (MemoryError / RecursionError / CancelledError).
        """
        app_state: AppState = state.app_state
        status = await _evaluate_readiness(app_state)
        return Response(
            content=ApiResponse(
                data=ReadinessProbe(
                    status=status.status,
                    version=status.version,
                    uptime_seconds=status.uptime_seconds,
                ),
            ),
            status_code=_status_code_for(status.status),
        )


class HealthController(Controller):
    """Authenticated component-health detail endpoint.

    Mirrors the readiness probe but returns the full per-component
    breakdown for operator tooling (the dashboard health popover). Gated
    by ``require_read_access`` so the operational topology is never
    exposed to unauthenticated callers (the public probe is
    :class:`ReadinessController`). Returns 200 when ready, 503 otherwise.
    """

    path = "/health"
    tags = ("health",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def health(
        self,
        state: State,
    ) -> Response[ApiResponse[ReadinessStatus]]:
        """Return the full component-health breakdown + 200/503.

        Returns:
            ``Response[ApiResponse[ReadinessStatus]]`` instance.

        Raises:
            BaseExceptionGroup: Re-raised only for fatal signals
                (MemoryError / RecursionError / CancelledError).
        """
        app_state: AppState = state.app_state
        status = await _evaluate_readiness(app_state)
        return Response(
            content=ApiResponse(data=status),
            status_code=_status_code_for(status.status),
        )
