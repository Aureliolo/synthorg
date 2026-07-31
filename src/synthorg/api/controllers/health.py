"""Liveness and readiness probe controllers.

* ``/healthz`` (liveness): always 200 while the event loop is
  turning; no dependency probes. Kubernetes-style supervisors use
  this to decide whether to restart the process.
* ``/readyz`` (readiness): 200 only when persistence + message
  bus are both healthy; otherwise 503. Used to gate traffic / block
  rollouts until dependencies are up.
"""

import asyncio
from enum import StrEnum
from typing import Literal

from litestar import Controller, Response, get
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field

from synthorg import __version__
from synthorg._core.features import require_service
from synthorg.api.controllers._backup_health import (
    BackupHealth,
    resolve_backup_health,
)
from synthorg.api.controllers._health_probes import (
    TelemetryStatus,
    memory_readiness,
    probe_persistence,
    probe_service,
    resolve_memory_state,
    resolve_telemetry_status,
)
from synthorg.api.controllers._memory_health import (
    MemoryHealth,
    MemoryState,
    memory_wiring_health,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import API_HEALTH_CHECK
from synthorg.providers.state import ProvidersStateSlice
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice, config_resolver_of

logger = get_logger(__name__)


class ReadinessOutcome(StrEnum):
    """Binary readiness outcome.

    Readiness is a pass/fail gate for supervisors; we deliberately
    drop the tri-state ``degraded`` value that the old ``/health``
    endpoint used -- a supervisor has no sensible action for it.
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
    supervisor decision depends on it. An operator reads the running version from
    the authenticated breakdown, or locally from the deployed image tag.

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
        providers: All tracked LLM providers reachable (no ``DOWN``
            status). ``None`` when no provider health tracker is
            wired (dev stacks without provider configuration).
        telemetry: Project telemetry delivery state.
        backup: Backup coverage for this boot, and the cause when there is
            none. Deliberately excluded from the ``status`` roll-up: a
            process with no backup coverage still serves traffic correctly,
            so flipping readiness would have a supervisor restart a healthy
            deployment into the same condition.
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
    memory: MemoryHealth = Field(
        description="Agent-memory substrate state",
    )
    backup: BackupHealth = Field(
        description="Backup coverage for this boot",
    )
    version: str = Field(description="Application version")
    uptime_seconds: float = Field(ge=0.0, description="Seconds since startup")


def _unavailable_status(app_state: AppState) -> ReadinessStatus:
    """Build a 503 ``unavailable`` readiness status.

    Used when the probe TaskGroup itself raises an unexpected error;
    we still want to emit a well-formed envelope so operator tooling
    can parse it, rather than letting a 500 surface.

    Returns:
        ``ReadinessStatus`` instance with every component unknown.
    """
    uptime = round(app_state.clock.monotonic() - app_state.startup_time, 2)
    # Wiring only: this path exists because the probe TaskGroup already
    # failed, so probing the backend again would be reporting on the
    # thing that just broke.
    memory = memory_wiring_health(app_state) or MemoryHealth(
        state=MemoryState.DEGRADED,
        backend=app_state.config.memory.backend,
        detail=(
            "A memory backend is wired but the readiness probe did not "
            "complete, so its live state is unknown."
        ),
    )
    return ReadinessStatus(
        status=ReadinessOutcome.UNAVAILABLE,
        persistence=None,
        message_bus=None,
        providers=None,
        telemetry=resolve_telemetry_status(app_state),
        memory=memory,
        # Read from the slice rather than left unknown: unlike the probed
        # components, this is a boot-time fact the failed fan-out did not
        # touch, so it stays reportable.
        backup=resolve_backup_health(app_state),
        version=__version__,
        uptime_seconds=uptime,
    )


def _status_code_for(outcome: ReadinessOutcome) -> int:
    """Map a readiness outcome to its HTTP status code.

    Returns:
        ``200`` when ready, ``503`` otherwise.
    """
    return 200 if outcome is ReadinessOutcome.OK else 503


async def _resolve_readiness_probe_timeout(app_state: AppState) -> float:
    """Resolve the readiness-probe timeout budget per probe.

    Reads ``api.readiness_probe_timeout_seconds`` through the live settings
    chain (DB > env > default) so an operator change applies without a
    restart. This setting is resolver-read-only (no bridge snapshot or
    subscriber), so the boot-config value is the only stable non-resolver
    source and is the correct fallback on a missing resolver or a resolver
    outage -- a settings-backend hiccup must not perturb the probe budget.

    Returns:
        The probe-timeout ceiling in seconds.

    Raises:
        CancelledError: Propagated when the resolver await is cancelled.
    """
    boot_value = app_state.config.api.readiness_probe_timeout_seconds
    if app_state.slice(SettingsStateSlice).config_resolver is None:
        return boot_value
    try:
        return await config_resolver_of(app_state).get_float(
            SettingNamespace.API.value,
            "readiness_probe_timeout_seconds",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_HEALTH_CHECK,
            setting="api.readiness_probe_timeout_seconds",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            fallback_seconds=boot_value,
        )
        return boot_value


async def _evaluate_readiness(app_state: AppState) -> ReadinessStatus:
    """Probe every configured dependency and compute the readiness status.

    Shared by the unauthenticated ``/readyz`` probe (which projects this
    onto a topology-free :class:`ReadinessProbe`) and the authenticated
    ``/health`` detail endpoint (which returns it verbatim).

    Returns:
        The full :class:`ReadinessStatus` (``unavailable`` with unknown
        components if the probe TaskGroup raises a non-fatal error or
        exceeds ``api.readiness_probe_timeout_seconds``).

    Raises:
        BaseExceptionGroup: Re-raised only for fatal signals
            (MemoryError / RecursionError / CancelledError).
    """
    probe_timeout = await _resolve_readiness_probe_timeout(app_state)
    try:
        # Bound the whole dependency fan-out: a single hung probe (a
        # wedged health_check that never returns) must yield a 503
        # ``unavailable`` verdict within the probe budget rather than
        # stalling /readyz past the orchestrator's readinessProbe
        # timeout. ``asyncio.timeout`` cancels the TaskGroup on expiry;
        # the resulting ``TimeoutError`` arrives wrapped in the group.
        async with asyncio.timeout(probe_timeout), asyncio.TaskGroup() as tg:
            persistence_task = tg.create_task(probe_persistence(app_state))
            bus_task = tg.create_task(
                probe_service(
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
                probe_service(
                    configured=app_state.slice(ProvidersStateSlice).health_tracker
                    is not None,
                    probe=_probe_providers,
                    component="providers",
                ),
            )
            # Inside the fan-out so a wedged memory store is bounded by
            # the same probe budget as every other dependency.
            memory_task = tg.create_task(resolve_memory_state(app_state))
    except TimeoutError:
        # The probe fan-out exceeded the budget; ``asyncio.timeout``
        # cancelled the TaskGroup and surfaced a bare ``TimeoutError``.
        # A timed-out probe is an unavailable verdict, not a 500.
        logger.warning(
            API_HEALTH_CHECK,
            component="readiness",
            status=ReadinessOutcome.UNAVAILABLE.value,
            error="readiness probe timed out",
            error_type="TimeoutError",
            timeout_seconds=probe_timeout,
        )
        return _unavailable_status(app_state)
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
    telemetry_status = resolve_telemetry_status(app_state)
    memory_health = memory_task.result()
    memory_ready = memory_readiness(memory_health)

    # Readiness is a pass/fail: every *configured* dependency must
    # report healthy. Unconfigured (None) is treated as not blocking
    # -- dev stacks without a bus still report ready.
    configured_checks = [
        v for v in (persistence_ok, bus_ok, providers_ok, memory_ready) if v is not None
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
        memory=memory_health.state.value,
    )
    return ReadinessStatus(
        status=outcome,
        persistence=persistence_ok,
        message_bus=bus_ok,
        providers=providers_ok,
        telemetry=telemetry_status,
        memory=memory_health,
        backup=resolve_backup_health(app_state),
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
                uptime_seconds=uptime,
            ),
        )


class ReadinessController(Controller):
    """Readiness probe endpoint.

    Intentionally unauthenticated (excluded from the auth middleware in
    ``middleware_factory``): supervisors and load-balancers must reach
    it without credentials to gate traffic. For that reason the body is
    deliberately topology-free and version-free, carrying only the binary
    ``ok`` / ``unavailable`` outcome plus uptime; the per-component
    breakdown (persistence / message bus / providers / telemetry) and the
    build version live behind authentication on ``GET /health``
    (:class:`HealthController`).
    Returns 200 when every configured dependency is healthy, else 503.
    """

    path = "/readyz"
    tags = ("health",)

    @get(guards=[per_op_rate_limit_from_policy("health.ready", key="ip")])
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

    @get(
        guards=[
            # Every call fans out to a live persistence health_check, a bus
            # health_check and a memory probe, and the dashboard polls this on an
            # interval per open tab, so it needs its own ceiling. Keyed per user
            # rather than per IP: several tabs behind one NAT are one operator's
            # dashboards, not a shared quota to fight over.
            per_op_rate_limit_from_policy("health.detail", key="user"),
        ],
    )
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
        http_status = _status_code_for(status.status)
        logger.debug(
            API_HEALTH_CHECK,
            handler="health",
            readiness=status.status.value,
            http_status=http_status,
        )
        return Response(
            content=ApiResponse(data=status),
            status_code=http_status,
        )
