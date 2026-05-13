"""Telemetry collector -- gathers curated metrics from runtime."""

import asyncio
import contextlib
import os
import platform
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.telemetry import (
    TELEMETRY_CLOSED,
    TELEMETRY_DEPLOYMENT_ID_CREATED,
    TELEMETRY_DEPLOYMENT_ID_LOADED,
    TELEMETRY_DISABLED,
    TELEMETRY_ENABLED,
    TELEMETRY_ENVIRONMENT_RESOLVED,
    TELEMETRY_EVENT_DEPLOYMENT_HEARTBEAT,
    TELEMETRY_EVENT_DEPLOYMENT_SESSION_SUMMARY,
    TELEMETRY_EVENT_DEPLOYMENT_SHUTDOWN,
    TELEMETRY_EVENT_DEPLOYMENT_STARTUP,
    TELEMETRY_HEARTBEAT_SENT,
    TELEMETRY_REPORT_FAILED,
    TELEMETRY_SESSION_SUMMARY_SENT,
    TELEMETRY_SHUTDOWN_WITHOUT_START,
    TELEMETRY_TOKEN_MISSING,
)
from synthorg.telemetry.config import DEFAULT_ENVIRONMENT, MAX_STRING_LENGTH
from synthorg.telemetry.host_info import DockerHostInfo, fetch_docker_info
from synthorg.telemetry.privacy import PrivacyScrubber, PrivacyViolationError
from synthorg.telemetry.protocol import TelemetryEvent, TelemetryReporter
from synthorg.telemetry.reporters import create_reporter
from synthorg.telemetry.reporters._embedded_token import is_token_embedded
from synthorg.telemetry.reporters.noop import NoopReporter

_ENV_OVERRIDE_VAR = "SYNTHORG_TELEMETRY_ENV"
"""Runtime override for :attr:`TelemetryConfig.environment`.

A non-empty value in this variable beats everything else so
operators can retag any deployment without rewriting config
files or rebuilding the image.
"""

_ENV_BAKED_VAR = "SYNTHORG_TELEMETRY_ENV_BAKED"
"""Image-baked fallback for :attr:`TelemetryConfig.environment`.

Set by ``docker/backend/Dockerfile``'s ``DEPLOYMENT_ENV`` build-arg.
Release-tag CI builds bake ``prod``; ``-dev.N`` pre-release tag
builds bake ``pre-release``; everything else (main pushes, PR
builds, local ``docker build``) bakes the Dockerfile default
``dev``. Operators that want to override per-deployment use
:data:`_ENV_OVERRIDE_VAR` -- the baked value is only a default.
"""

_CI_ENV_MARKERS: tuple[str, ...] = (
    "CI",
    "GITLAB_CI",
    "BUILDKITE",
    "JENKINS_URL",
)
"""Well-known CI markers consulted when no operator override is set.

Each entry is one that runners set automatically without operator
action. GitHub Actions sets ``CI=true`` (covered by the first
entry). RunPod's ``RUNPOD_*`` family is handled separately via
:data:`_CI_ENV_PREFIXES`.
"""

_CI_ENV_PREFIXES: tuple[str, ...] = ("RUNPOD_",)
"""Env var prefixes that indicate a CI / ephemeral runner context.

Stored as a tuple because :meth:`str.startswith` accepts a tuple of
candidate prefixes natively; any future prefix (e.g. ``MODAL_``,
``REPLIT_``) goes here without touching :func:`_looks_like_ci`.
"""


_DEPLOYMENT_ID_LOAD_TIMEOUT_SECONDS: float = 5.0
"""Hard deadline for the deployment-id ``asyncio.to_thread`` boundary.

Above this, the load is abandoned and :class:`start` falls back to a
freshly-generated UUID (logged at WARNING with
``using_generated_id=True``). Defends against hung NFS / stale-handle
data dirs starving the executor pool under thundering-herd startup.
"""


_PEER_READ_RETRY_ATTEMPTS: int = 3
"""Re-read attempts when a peer wins the ``O_CREAT|O_EXCL`` race.

Defends against the partial-write window where the peer has just
created the file but hasn't yet finished ``write()`` -- our re-read
sees an empty / truncated string. Retries inside the same
``to_thread`` boundary so the OS-level race semantics stay atomic.
"""


_PEER_READ_RETRY_DELAY_SECONDS: float = 0.005
"""Sleep between peer-read retries (5 ms). Short enough to converge
within a typical write window, long enough to yield CPU to the peer."""


_TEMP_ROOT: str | None
try:
    _TEMP_ROOT = os.path.normcase(
        os.path.normpath(str(Path(tempfile.gettempdir()))),
    )
except OSError, RuntimeError:
    # Sandboxed / security-locked environments may forbid temp access.
    # Fall back to ``/data``-only allow-list at I/O time.
    _TEMP_ROOT = None
"""Pre-computed normalised path of ``tempfile.gettempdir()``.

Cached at module load so the sync helper does not re-resolve the
temp dir on every ``start()``. The fallback to ``None`` activates
the data-root allow-list path in the helper.
"""


def _looks_like_ci(environ: Mapping[str, str] | None = None) -> bool:
    """Return ``True`` when the process runs under a known CI runner.

    A non-empty value in any :data:`_CI_ENV_MARKERS` or the presence
    of any env var whose name starts with an entry in
    :data:`_CI_ENV_PREFIXES` is enough. Accepts an optional mapping
    so tests can exercise the decision without mutating
    :data:`os.environ`.
    """
    env = environ if environ is not None else os.environ
    for marker in _CI_ENV_MARKERS:
        if env.get(marker, "").strip():
            return True
    return any(name.startswith(_CI_ENV_PREFIXES) for name in env)


def _resolve_environment(
    config_environment: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Pick the effective deployment environment tag.

    Priority order (first match wins):

    1. :data:`_ENV_OVERRIDE_VAR` -- explicit operator override.
    2. CI auto-detection via :func:`_looks_like_ci` -> ``"ci"``.
    3. :data:`_ENV_BAKED_VAR` -- Dockerfile-baked default for this
       image (``prod`` / ``pre-release`` / ``dev``).
    4. The parsed :attr:`TelemetryConfig.environment` -- which
       itself falls back to :data:`DEFAULT_ENVIRONMENT` when not
       set.

    Strings are trimmed and truncated at
    :data:`MAX_STRING_LENGTH` chars to match the
    :class:`PrivacyScrubber` cap; whitespace-only values at any
    level are ignored so they cannot mask a lower-priority signal.
    Falls back to :data:`DEFAULT_ENVIRONMENT` when the parsed
    config value is blank after stripping.
    """
    env = environ if environ is not None else os.environ

    override = env.get(_ENV_OVERRIDE_VAR, "").strip()
    if override:
        return override[:MAX_STRING_LENGTH]

    if _looks_like_ci(env):
        return "ci"

    baked = env.get(_ENV_BAKED_VAR, "").strip()
    if baked:
        return baked[:MAX_STRING_LENGTH]

    stripped_config = config_environment.strip()
    if stripped_config:
        return stripped_config[:MAX_STRING_LENGTH]
    return DEFAULT_ENVIRONMENT


if TYPE_CHECKING:
    from synthorg.telemetry.config import TelemetryConfig

logger = get_logger(__name__)


@dataclass(frozen=True)
class _HeartbeatParams:
    """Parameter bundle for heartbeat events."""

    agent_count: int = 0
    department_count: int = 0
    team_count: int = 0
    template_name: str = ""
    persistence_backend: str = "sqlite"
    memory_backend: str = "mem0"
    features_enabled: str = ""


@dataclass(frozen=True)
class _SessionSummaryParams:
    """Parameter bundle for session summary events."""

    tasks_created: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    error_rate_limit: int = 0
    error_timeout: int = 0
    error_connection: int = 0
    error_internal: int = 0
    error_validation: int = 0
    error_other: int = 0
    provider_count: int = 0
    topology_hierarchical: int = 0
    topology_parallel: int = 0
    topology_sequential: int = 0
    topology_auto: int = 0
    meetings_held: int = 0
    delegations_executed: int = 0


HeartbeatSnapshotProvider = Callable[[], _HeartbeatParams]
SessionSummarySnapshotProvider = Callable[[], _SessionSummaryParams]


class TelemetryCollector:
    """Gathers curated metrics and sends via the reporter.

    The collector is the single entry point for all telemetry.  It:

    1. Reads opt-in config (env var > config file).
    2. Creates the appropriate reporter (noop when disabled).
    3. Validates every event through ``PrivacyScrubber``.
    4. Manages the heartbeat schedule.
    5. Sends a session summary on shutdown.

    Args:
        config: Telemetry configuration.
        data_dir: Directory to persist the anonymous deployment ID.
        heartbeat_snapshot_provider: Optional callable returning the
            current ``_HeartbeatParams`` snapshot.  Used by the
            internal heartbeat loop so emitted events contain real
            runtime metrics instead of zero defaults.
        session_summary_snapshot_provider: Optional callable returning
            the current ``_SessionSummaryParams`` snapshot.  Used by
            ``shutdown()`` to emit aggregated session metrics.
    """

    def __init__(
        self,
        config: TelemetryConfig,
        data_dir: Path,
        heartbeat_snapshot_provider: HeartbeatSnapshotProvider | None = None,
        session_summary_snapshot_provider: SessionSummarySnapshotProvider | None = None,
    ) -> None:
        """Wire the collector to its reporter and resolve runtime env.

        Takes ``config.enabled`` as-given; the
        ``SYNTHORG_TELEMETRY_ENABLED`` precedence is resolved
        upstream by the wiring layer (see
        :func:`synthorg.api.app_builders._build_telemetry_collector`).
        The constructor only resolves the deployment-environment tag
        through the four-level chain in :func:`_resolve_environment`
        (operator override -> CI detection -> Dockerfile-baked default
        -> parsed config). The constructor performs **zero filesystem
        I/O**; loading or creating the anonymous ``deployment_id`` is
        deferred to :meth:`start`. The load itself runs outside the
        event loop's thread via ``asyncio.to_thread``. A disabled
        collector still leaves no on-disk trace.

        Args:
            config: Parsed telemetry configuration from
                :class:`TelemetryConfig`.
            data_dir: Directory used to persist the deployment ID
                when telemetry is enabled.
            heartbeat_snapshot_provider: Optional callable returning
                the current :class:`_HeartbeatParams` snapshot; used
                by the heartbeat loop to attach fresh aggregate
                metrics to each heartbeat event.
            session_summary_snapshot_provider: Optional callable
                returning the current :class:`_SessionSummaryParams`
                snapshot; used by :meth:`shutdown` to emit the final
                session summary.
        """
        # ``config.enabled`` is taken as-given; precedence resolution
        # for the registered ``telemetry.enabled`` setting (DB > env >
        # YAML > default) happens upstream in
        # ``synthorg.api.app_builders._build_telemetry_collector``,
        # which reads ``SYNTHORG_TELEMETRY_ENABLED`` once and folds the
        # value into the parsed ``TelemetryConfig`` before
        # construction. This collector intentionally does not
        # re-apply precedence here so the audit trail stays single-
        # sourced and a future routing through ``SettingsService`` /
        # ``ConfigResolver`` can replace the env read in one place.
        # Resolve the effective deployment-environment tag through
        # the four-level chain (operator override -> CI detection ->
        # Dockerfile-baked default -> parsed config). See
        # :func:`_resolve_environment` for the full priority contract.
        resolved_env = _resolve_environment(config.environment)
        if resolved_env != config.environment:
            logger.info(
                TELEMETRY_ENVIRONMENT_RESOLVED,
                configured_environment=config.environment,
                resolved_environment=resolved_env,
            )
            config = config.model_copy(update={"environment": resolved_env})

        self._config = config
        self._data_dir = data_dir
        self._scrubber = PrivacyScrubber()
        self._reporter: TelemetryReporter = create_reporter(config)
        self._deployment_id: str | None = None
        self._started_at = datetime.now(UTC)
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_snapshot_provider = heartbeat_snapshot_provider
        self._session_summary_snapshot_provider = session_summary_snapshot_provider
        # Eager init: shutdown() must be safe before any start() call.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        # ``shutdown()`` flips this so a stray second ``start()`` is
        # rejected loudly rather than silently re-using a torn-down
        # reporter. Restart-after-shutdown is not a supported flow:
        # the reporter holds transports that were aclose()'d in
        # shutdown(), and the heartbeat task cannot be re-scheduled
        # safely on a different loop without re-validating that
        # contract end-to-end.
        self._closed: bool = False
        # Tracks whether ``start()`` deliberately bailed out because the
        # build artifact ships the sentinel token. Set so ``shutdown()``
        # can skip the misleading ``TELEMETRY_SHUTDOWN_WITHOUT_START``
        # warning that would otherwise fire (deployment_id stays None
        # in this branch by design, not by failure).
        self._token_missing_at_start: bool = False

        if not config.enabled:
            # Operator-visible signal that the collector booted in the
            # disabled path. INFO (not DEBUG) so a single line lands in
            # the main log on every boot -- the issue's acceptance
            # criterion is "exactly one INFO at startup, zero per-cycle
            # warnings". The heartbeat task is also skipped in start()
            # so no reporter cycles run while disabled.
            logger.info(TELEMETRY_DISABLED)

    @property
    def deployment_id(self) -> str | None:
        """The anonymous deployment UUID, or ``None`` when disabled."""
        return self._deployment_id

    @property
    def enabled(self) -> bool:
        """Whether telemetry is enabled."""
        return self._config.enabled

    @property
    def is_functional(self) -> bool:
        """Whether telemetry is both opted in AND the reporter can deliver.

        Returns ``False`` when telemetry is opt-out, and also when the
        operator opted in but :func:`create_reporter` fell back to
        :class:`NoopReporter` (missing ``logfire`` extra, reporter
        construction failure, or explicit ``TelemetryBackend.NOOP``).
        This is what the health endpoint surfaces: ``enabled`` alone
        would lie about delivery whenever the reporter silently
        degraded to noop.
        """
        return self._config.enabled and not isinstance(
            self._reporter,
            NoopReporter,
        )

    def _log_token_missing(self) -> None:
        """Log the missing-token bail-out at the right severity.

        Only a production release wheel is expected to carry an
        embedded token; in dev / pre-release / CI the operator routinely
        enables telemetry against a source install whose build pipeline
        never ran the embedder. ERROR every boot there buries real
        production-only failures. Keep ERROR for
        ``environment == "prod"`` and downgrade everything else to INFO
        with an actionable detail message.
        """
        if self._config.environment == "prod":
            logger.error(
                TELEMETRY_TOKEN_MISSING,
                detail=(
                    "build artifact missing embedded token; rebuild "
                    "release wheel with LOGFIRE_PROJECT_TOKEN CI secret"
                ),
                backend=self._config.backend.value,
                environment=self._config.environment,
            )
        else:
            logger.info(
                TELEMETRY_TOKEN_MISSING,
                detail=(
                    "telemetry token not embedded in this build; "
                    "telemetry will stay disabled. To exercise the "
                    "reporter locally run scripts/embed_logfire_token.py "
                    "before starting the backend."
                ),
                backend=self._config.backend.value,
                environment=self._config.environment,
            )

    async def start(self) -> None:
        """Load the deployment ID and start the periodic heartbeat.

        Performs the lifecycle transition deferred from ``__init__``.
        The deployment-id load runs here through
        ``asyncio.to_thread`` with a hard deadline so the event
        loop's thread is never blocked on filesystem I/O. Idempotent
        and safe under concurrent callers: serialised by a lifecycle
        lock so the load, the startup event, and heartbeat task
        creation happen atomically. The load is wrapped in
        defence-in-depth try/except so a contract violation in the
        sync helper or a thread-pool timeout never crashes
        ``start()``.
        """
        async with self._lifecycle_lock:
            if self._closed:
                # Restart after a clean shutdown is not supported;
                # raising loudly is better than silently using a
                # torn-down reporter / a stale heartbeat task slot.
                msg = "TelemetryCollector.start() called after shutdown()"
                raise RuntimeError(msg)
            if not self._config.enabled:
                return
            # Build artifact ships the sentinel token. Operator opted in
            # to LOGFIRE, but the wheel was published without the
            # LOGFIRE_PROJECT_TOKEN CI secret -- log ONCE at ERROR
            # severity so operators can escalate to the build pipeline,
            # then return without starting the heartbeat loop. This is
            # the fix for the "every reporter cycle" warning spam: with
            # no loop, no cycles, no spam. The factory has already
            # returned a NoopReporter for this state. The check is
            # scoped to ``TelemetryBackend.LOGFIRE`` so the noop
            # backend (used by tests and operators who explicitly
            # opt out of Logfire) is unaffected.
            from synthorg.telemetry.config import (  # noqa: PLC0415
                TelemetryBackend,
            )

            if self._token_missing_at_start:
                # Re-entry after a prior bail-out; the ERROR has
                # already been logged once. Stay quiet to honour the
                # "exactly one startup signal" contract and never
                # start the heartbeat task on this branch.
                return
            if (
                self._config.backend == TelemetryBackend.LOGFIRE
                and not is_token_embedded()
            ):
                self._token_missing_at_start = True
                self._log_token_missing()
                return
            if self._heartbeat_task is not None and not self._heartbeat_task.done():
                return
            if self._deployment_id is None:
                # Pre-generate the candidate UUID and pass it to the
                # worker thread. ``asyncio.wait_for`` cancels the
                # awaiting coroutine on timeout but does NOT cancel
                # the underlying thread; if the worker eventually
                # completes after the deadline it will persist to
                # disk. Sharing the candidate guarantees the in-memory
                # fallback and any post-deadline disk write agree on
                # the same UUID, so a subsequent restart cannot load
                # a different on-disk value and split-brain the
                # deployment identity across boots.
                candidate_id = str(uuid.uuid4())
                try:
                    self._deployment_id = await asyncio.wait_for(
                        self._load_or_create_deployment_id(candidate_id),
                        timeout=_DEPLOYMENT_ID_LOAD_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    # Hung filesystem (stale NFS handle, slow disk).
                    # Fall back to the candidate UUID so the collector
                    # can still emit events; surface the splinter
                    # state via ``using_generated_id=True``.
                    self._deployment_id = candidate_id
                    logger.warning(
                        TELEMETRY_REPORT_FAILED,
                        detail="deployment_id_load_timeout",
                        timeout_seconds=_DEPLOYMENT_ID_LOAD_TIMEOUT_SECONDS,
                        using_generated_id=True,
                    )
                except MemoryError, RecursionError:
                    # System-level errors must propagate so operators
                    # see the real condition; downgrading them to a
                    # generated UUID would mask runaway-recursion or
                    # out-of-memory states behind a healthy-looking
                    # telemetry boot.
                    raise
                except Exception as exc:
                    # The sync helper is documented to never raise,
                    # but a future regression must not crash start()
                    # and leak the heartbeat task slot. Match the
                    # pattern used elsewhere in this module: warning
                    # severity with a categorical detail + error_type
                    # rather than ``logger.exception`` (which would
                    # attach a traceback that adds no actionable
                    # context beyond the structured fields).
                    self._deployment_id = candidate_id
                    logger.warning(
                        TELEMETRY_REPORT_FAILED,
                        detail="deployment_id_load_unexpected_error",
                        error_type=type(exc).__name__,
                        using_generated_id=True,
                    )
                logger.info(
                    TELEMETRY_ENABLED,
                    backend=self._config.backend.value,
                    deployment_id=self._deployment_id,
                )
            await self._send_startup_event()
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(),
                name="telemetry-heartbeat",
            )

    async def shutdown(self) -> None:
        """Cancel heartbeat, send session summary, shut down reporter.

        Each step is wrapped in its own try/except so a failure in
        one stage never aborts the rest of the cleanup sequence.
        Serialised with ``start()`` via the lifecycle lock.
        """
        async with self._lifecycle_lock:
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
                self._heartbeat_task = None

            # Only send events when ``start()`` actually loaded the
            # deployment ID; emitting through ``_build_event`` without
            # one would trip its non-None assertion. ``start()``
            # never being called for an enabled collector is unusual
            # (constructor -> shutdown without start) but legal under
            # the new lifecycle, so guard explicitly. Log a WARNING
            # in the unloaded-but-enabled branch so operators have a
            # signal that telemetry initialisation failed silently.
            # The token-missing branch is excluded: ``start()`` already
            # logged a single-shot ERROR (TELEMETRY_TOKEN_MISSING) and
            # deployment_id is None by design, not by failure.
            if (
                self._config.enabled
                and self._deployment_id is None
                and not self._token_missing_at_start
            ):
                logger.warning(
                    TELEMETRY_SHUTDOWN_WITHOUT_START,
                    note="shutdown invoked before deployment ID loaded",
                )
            if self._config.enabled and self._deployment_id is not None:
                params: _SessionSummaryParams | None = None
                if self._session_summary_snapshot_provider is not None:
                    try:
                        params = self._session_summary_snapshot_provider()
                    except Exception as exc:
                        logger.warning(
                            TELEMETRY_REPORT_FAILED,
                            detail="session_summary_snapshot_failed",
                            error_type=type(exc).__name__,
                        )

                try:
                    await self.send_session_summary(params)
                except Exception as exc:
                    logger.warning(
                        TELEMETRY_REPORT_FAILED,
                        detail="send_session_summary_failed",
                        error_type=type(exc).__name__,
                    )

                try:
                    await self._send_shutdown_event()
                except Exception as exc:
                    logger.warning(
                        TELEMETRY_REPORT_FAILED,
                        detail="send_shutdown_event_failed",
                        error_type=type(exc).__name__,
                    )

            try:
                await self._reporter.shutdown()
            except Exception as exc:
                logger.warning(
                    TELEMETRY_REPORT_FAILED,
                    detail="reporter_shutdown_failed",
                    error_type=type(exc).__name__,
                )

            # Mark the collector terminal AFTER teardown so a stray
            # ``start()`` call cannot resurrect a torn-down reporter.
            self._closed = True
            logger.info(TELEMETRY_CLOSED)

    async def send_heartbeat(
        self,
        params: _HeartbeatParams | None = None,
    ) -> None:
        """Send a heartbeat event with current deployment metrics.

        Returns early if the deployment ID has not been loaded yet
        (caller skipped ``start()``). The internal heartbeat loop
        only runs after ``start()`` populates the ID, but external
        callers may invoke this without going through the lifecycle;
        guard the ``_build_event`` assertion explicitly so misuse
        degrades to a no-op rather than a crash.
        """
        if not self._config.enabled:
            return
        if self._deployment_id is None:
            return
        p = params or _HeartbeatParams()
        uptime = self._uptime_hours()

        event = self._build_event(
            TELEMETRY_EVENT_DEPLOYMENT_HEARTBEAT,
            agent_count=p.agent_count,
            department_count=p.department_count,
            team_count=p.team_count,
            template_name=p.template_name,
            persistence_backend=p.persistence_backend,
            memory_backend=p.memory_backend,
            features_enabled=p.features_enabled,
            uptime_hours=round(uptime, 2),
        )
        if await self._send(event):
            logger.debug(TELEMETRY_HEARTBEAT_SENT)

    async def send_session_summary(
        self,
        params: _SessionSummaryParams | None = None,
    ) -> None:
        """Send a session summary event with aggregate metrics.

        Returns early if the deployment ID has not been loaded yet
        (caller skipped ``start()``). See :meth:`send_heartbeat` for
        the full rationale; same guard applies.
        """
        if not self._config.enabled:
            return
        if self._deployment_id is None:
            return
        p = params or _SessionSummaryParams()
        uptime = self._uptime_hours()

        event = self._build_event(
            TELEMETRY_EVENT_DEPLOYMENT_SESSION_SUMMARY,
            tasks_created=p.tasks_created,
            tasks_completed=p.tasks_completed,
            tasks_failed=p.tasks_failed,
            error_rate_limit=p.error_rate_limit,
            error_timeout=p.error_timeout,
            error_connection=p.error_connection,
            error_internal=p.error_internal,
            error_validation=p.error_validation,
            error_other=p.error_other,
            provider_count=p.provider_count,
            topology_hierarchical=p.topology_hierarchical,
            topology_parallel=p.topology_parallel,
            topology_sequential=p.topology_sequential,
            topology_auto=p.topology_auto,
            meetings_held=p.meetings_held,
            delegations_executed=p.delegations_executed,
            uptime_hours=round(uptime, 2),
        )
        if await self._send(event):
            logger.debug(TELEMETRY_SESSION_SUMMARY_SENT)

    def _uptime_hours(self) -> float:
        """Return elapsed hours since collector was initialised."""
        delta = datetime.now(UTC) - self._started_at
        return delta.total_seconds() / 3600

    def _build_event(
        self,
        event_type: str,
        **properties: int | float | str | bool,
    ) -> TelemetryEvent:
        """Construct a ``TelemetryEvent`` with runtime metadata.

        Only called when telemetry is enabled (deployment ID is set).
        """
        assert self._deployment_id is not None  # noqa: S101
        vi = sys.version_info
        return TelemetryEvent(
            event_type=event_type,
            deployment_id=self._deployment_id,
            synthorg_version=_get_version(),
            python_version=f"{vi.major}.{vi.minor}.{vi.micro}",
            os_platform=platform.system(),
            environment=self._config.environment,
            timestamp=datetime.now(UTC),
            properties=properties,
        )

    async def _send(self, event: TelemetryEvent) -> bool:
        """Validate and send a telemetry event.

        Logs and drops events that fail privacy validation.
        Logs and suppresses reporter errors (telemetry must not
        affect the main application).

        Returns:
            ``True`` if the event was delivered, ``False`` otherwise.
        """
        try:
            self._scrubber.validate(event)
        except PrivacyViolationError as exc:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                event_type=event.event_type,
                detail="privacy_violation",
                error_type=type(exc).__name__,
                error_code="PRIVACY_VIOLATION",
            )
            return False

        try:
            await self._reporter.report(event)
        except Exception as exc:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                event_type=event.event_type,
                error_type=type(exc).__name__,
                error_code="REPORTER_BACKEND_FAILURE",
            )
            return False

        return True

    async def _send_startup_event(self) -> None:
        """Send an initial ``deployment.startup`` event.

        Also fetches the telemetry-safe Docker daemon ``/info``
        snapshot so dashboards can split deployments by host OS /
        kernel / Docker version / storage driver / NVIDIA-runtime
        availability without joining on a separate system.

        Short-circuits when :attr:`is_functional` is ``False``;
        the reporter is a :class:`NoopReporter`, so emitting the
        event would be discarded anyway, and the Docker socket
        probe (which crosses the ``asyncio.to_thread`` boundary
        and potentially reaches for ``/var/run/docker.sock``) is
        wasted work.

        :func:`fetch_docker_info` is designed to never raise (every
        failure collapses to a ``docker_info_available=False``
        marker). The outer ``try`` below is a belt-and-suspenders
        guard: a regression in the helper or an unexpected
        exception type must not abort the startup event, since the
        startup event is the primary deployment-identification
        signal in Logfire and we'd rather ship it without docker
        info than not ship it at all.
        """
        if not self.is_functional:
            return

        try:
            docker_info: DockerHostInfo = await fetch_docker_info()
        except Exception as exc:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="docker_info_fetch_unexpected_exception",
                error_type=type(exc).__name__,
            )
            docker_info = {
                "docker_info_available": False,
                "docker_info_unavailable_reason": "daemon_unreachable",
            }
        event = self._build_event(
            TELEMETRY_EVENT_DEPLOYMENT_STARTUP,
            agent_count=0,
            department_count=0,
            template_name="",
            persistence_backend="sqlite",
            memory_backend="mem0",
            **docker_info,
        )
        await self._send(event)

    async def _send_shutdown_event(self) -> None:
        """Send a deployment.shutdown event with uptime."""
        event = self._build_event(
            TELEMETRY_EVENT_DEPLOYMENT_SHUTDOWN,
            uptime_hours=round(self._uptime_hours(), 2),
            graceful=True,
        )
        await self._send(event)

    async def _heartbeat_loop(self) -> None:
        """Periodically send heartbeat events until cancelled.

        Catches and logs non-cancellation exceptions so the loop
        continues on transient failures.  ``CancelledError`` is
        re-raised for graceful shutdown.
        """
        interval = self._config.heartbeat_interval_hours * 3600
        # lint-allow: long-running-loop-kill-switch -- stop()/cancel drives shutdown.
        while True:
            try:
                await asyncio.sleep(interval)
                params = (
                    self._heartbeat_snapshot_provider()
                    if self._heartbeat_snapshot_provider is not None
                    else None
                )
                await self.send_heartbeat(params)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    TELEMETRY_REPORT_FAILED,
                    detail="heartbeat_loop",
                    error_type=type(exc).__name__,
                )

    async def _load_or_create_deployment_id(self, candidate_id: str) -> str:
        """Load deployment ID from file or persist ``candidate_id`` (async).

        Offloads the entire path-validation + filesystem sequence to
        ``asyncio.to_thread`` so the event loop's thread never blocks
        on disk. Single ``to_thread`` boundary keeps the
        read + atomic-create + peer-recover sequence on one OS
        thread so the ``O_CREAT|O_EXCL`` race semantics are
        preserved end-to-end; splitting the helper across multiple
        ``to_thread`` calls would let two coroutines interleave
        between read and create.

        ``candidate_id`` is the UUID the *caller* generated before
        spawning the worker. Passing it down ensures the timeout
        fallback in ``start()`` and the value the worker eventually
        persists agree on the same string, so the deployment ID
        cannot split-brain between the in-memory value and the
        on-disk file when the worker outlives the ``asyncio.wait_for``
        deadline.

        Returns a valid UUID string in all cases (never raises).
        Logs warnings on I/O errors.
        """
        return await asyncio.to_thread(
            _load_or_create_deployment_id_sync, self._data_dir, candidate_id
        )


def _load_or_create_deployment_id_sync(
    data_dir: Path,
    candidate_id: str,
) -> str:
    """Synchronous path-validate + load + atomic-create + peer-recover.

    Runs inside ``asyncio.to_thread`` so the event loop's thread is
    never blocked on filesystem I/O. The full sequence
    (path validation -> existence probe -> read -> ``O_CREAT|O_EXCL``
    create -> peer re-read on race, with retry on partial-write) lives
    in one helper so the OS-level race semantics are preserved
    end-to-end; splitting the helper across multiple ``to_thread``
    calls would let two coroutines interleave between read and create.

    ``candidate_id`` is the UUID the async caller pre-generated. It
    is the value persisted on the create branch, so the timeout
    fallback in ``start()`` and any post-deadline disk write the
    worker may still produce agree on the same string.

    Applies the OWASP path-injection recipe (``os.path.normpath`` +
    :py:meth:`str.startswith` on the normalised full path +
    trusted-root allow-list) immediately before the filesystem
    operations. The duplicate of
    :func:`synthorg.api.app._resolve_memory_dir` is deliberate
    defence-in-depth: ``normpath`` collapses ``..``/redundant
    separators that a caller constructing ``TelemetryCollector``
    directly could otherwise smuggle past ``data_dir``, and the
    startswith check is the sanitiser CodeQL's ``py/path-injection``
    query tracks across the sinks below.

    The whole body is wrapped in a top-level ``try/except Exception``
    so a future contract violation (unexpected exception type) cannot
    bubble out of the ``to_thread`` boundary and crash ``start()``.
    """
    try:
        id_path_str = _resolve_telemetry_id_path(data_dir)
        if id_path_str is None:
            return candidate_id
        existing = _read_existing_deployment_id(id_path_str)
        if existing is not None:
            return existing
        return _atomic_create_or_recover(id_path_str, candidate_id)
    except MemoryError, RecursionError:
        # System-level errors must propagate so operators see the
        # real condition; downgrading them to a generated UUID would
        # mask out-of-memory or runaway-recursion behind a healthy
        # telemetry boot.
        raise
    except Exception as exc:
        # Belt-and-suspenders: each helper is documented to never
        # raise, but a future regression must not bubble an exception
        # across the ``to_thread`` boundary. Fall back to the candidate
        # UUID. Warning, not exception, to match the structured-log
        # pattern used elsewhere in the module (categorical detail +
        # error_type, no traceback).
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="deployment_id_load_unexpected_helper_error",
            error_type=type(exc).__name__,
            using_generated_id=True,
        )
    return candidate_id


def _resolve_telemetry_id_path(data_dir: Path) -> str | None:
    """Build + validate the on-disk path for the deployment-id file.

    Returns the sanitised path string if it is a strict descendant
    of a trusted root (``/data`` or the module-cached temp root), or
    ``None`` (with a structured warning) if not. Implements the OWASP
    path-injection recipe + symlink resolution:

    * ``os.path.normpath`` collapses ``..`` and redundant separators
      so a caller cannot smuggle traversal through the ``data_dir``
      argument.
    * ``os.path.normcase`` lower-cases on Windows so the comparison
      is case-insensitive where the filesystem is.
    * ``os.path.realpath`` follows symbolic links and resolves the
      *actual* target. Without this step, a symlink under ``/data``
      pointing to ``/etc/passwd`` would pass a normpath-only prefix
      check yet open a path outside the trusted root, defeating the
      whole guard.
    * The ``startswith(root + sep)`` comparison after realpath is the
      sanitiser pattern CodeQL's ``py/path-injection`` query tracks
      across the sinks downstream.
    """
    id_path_str = os.path.normcase(
        os.path.normpath(
            os.path.join(  # noqa: PTH118
                os.fspath(data_dir),
                "telemetry_id",
            ),
        ),
    )
    # Follow symlinks on the candidate path AND on the trusted roots
    # so the comparison is between two real (post-realpath) paths.
    # ``realpath`` of a non-existent path returns the input with each
    # existing component resolved, so the parent-dir resolution still
    # catches a symlinked ``data_dir``.
    resolved_id_path = os.path.normcase(os.path.realpath(id_path_str))
    data_root = os.path.normcase(os.path.realpath(str(Path("/data"))))
    temp_root = (
        os.path.normcase(os.path.realpath(_TEMP_ROOT))
        if _TEMP_ROOT is not None
        else None
    )
    if not (
        resolved_id_path.startswith(data_root + os.sep)
        or (temp_root is not None and resolved_id_path.startswith(temp_root + os.sep))
    ):
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="data_dir_not_trusted",
            value=id_path_str,
            using_generated_id=True,
        )
        return None
    return id_path_str


def _read_existing_deployment_id(id_path_str: str) -> str | None:
    """Return a valid UUID from the on-disk file, or ``None`` if absent / corrupt.

    Corrupt files (non-UUID content) are unlinked so the
    atomic-create branch can write a fresh value; without this
    repair every subsequent boot would re-detect the same
    corruption and keep falling back to a generated ID,
    permanently fragmenting deployment telemetry until an
    operator manually deleted the file. Read errors are logged
    with a categorical detail and treated as "not present" so
    the create path runs.
    """
    try:
        if not os.path.exists(id_path_str):  # noqa: PTH110
            return None
        with open(id_path_str, encoding="utf-8") as fh:  # noqa: PTH123
            stored = fh.read().strip()
    except OSError as exc:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="deployment_id_read",
            error_type=type(exc).__name__,
        )
        return None
    if not stored:
        # An empty file usually means a peer is mid-write between
        # ``O_CREAT|O_EXCL`` and the actual content write. Re-read
        # with the partial-write retry first; if the peer never
        # finishes, treat the file as corrupt and unlink it so the
        # atomic-create branch can repair it. Without this, every
        # subsequent boot would see the same empty file, hit
        # ``FileExistsError`` on create, retry the peer-read,
        # return ``None``, and end up with the candidate UUID in
        # memory while the empty file persisted on disk -- the
        # exact "deployment IDs churn indefinitely" failure mode.
        peer_id = _read_peer_deployment_id(id_path_str)
        if peer_id is not None:
            logger.info(
                TELEMETRY_DEPLOYMENT_ID_LOADED,
                deployment_id=peer_id,
            )
            return peer_id
        with contextlib.suppress(OSError):
            os.unlink(id_path_str)  # noqa: PTH108
        return None
    try:
        uuid.UUID(stored)
    except ValueError:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="deployment_id_invalid",
            error_type="ValueError",
        )
        with contextlib.suppress(OSError):
            os.unlink(id_path_str)  # noqa: PTH108
        return None
    logger.info(
        TELEMETRY_DEPLOYMENT_ID_LOADED,
        deployment_id=stored,
    )
    return stored


def _atomic_create_or_recover(id_path_str: str, candidate_id: str) -> str:
    """Try ``O_CREAT|O_EXCL`` write; on race, re-read the peer's UUID.

    Wins-or-loses the race atomically so concurrent startups never
    overwrite each other's freshly-written UUID. On ``FileExistsError``
    a peer wrote first; the partial-write retry re-reads with a
    short backoff inside the same ``to_thread`` so the OS-level
    race semantics are preserved end-to-end.

    Returns the successfully-persisted UUID (either ``candidate_id``
    on the win or the peer's UUID on the loss) or ``candidate_id``
    if the write failed without producing a peer file we can read.
    """
    try:
        os.makedirs(  # noqa: PTH103
            os.path.dirname(id_path_str),  # noqa: PTH120
            exist_ok=True,
        )
        fd = os.open(
            id_path_str,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        fd_owned = True
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd_owned = False
                fh.write(candidate_id)
        except BaseException:
            if fd_owned:
                os.close(fd)
            raise
    except FileExistsError:
        peer_id = _read_peer_deployment_id(id_path_str)
        if peer_id is not None:
            logger.info(
                TELEMETRY_DEPLOYMENT_ID_LOADED,
                deployment_id=peer_id,
            )
            return peer_id
        return candidate_id
    except OSError as exc:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="deployment_id_write",
            error_type=type(exc).__name__,
            using_generated_id=True,
        )
        return candidate_id
    logger.info(
        TELEMETRY_DEPLOYMENT_ID_CREATED,
        deployment_id=candidate_id,
    )
    return candidate_id


def _read_peer_deployment_id(id_path_str: str) -> str | None:
    """Re-read a peer-created ID file with retry on partial writes.

    Defends against the window where a peer has just won the
    ``O_CREAT|O_EXCL`` race but has not yet finished ``write()``
    (the file exists but is empty or truncated). Retries up to
    :data:`_PEER_READ_RETRY_ATTEMPTS` times with
    :data:`_PEER_READ_RETRY_DELAY_SECONDS` between attempts.

    Returns the peer's UUID on success, ``None`` if all attempts
    return empty / corrupt / unreadable. Distinguishes the failure
    modes (file deleted, permission denied, decode error, validation
    error) in the logs so operators can tell "peer file disappeared"
    from "peer wrote garbage".
    """
    # See docs/reference/retry-patterns.md: Pattern A -- transient I/O.
    for attempt in range(_PEER_READ_RETRY_ATTEMPTS):
        try:
            with open(id_path_str, encoding="utf-8") as fh:  # noqa: PTH123
                stored = fh.read().strip()
        except FileNotFoundError:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="deployment_id_peer_file_deleted",
                error_type="FileNotFoundError",
                attempt=attempt,
            )
            return None
        except PermissionError:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="deployment_id_peer_file_unreadable",
                error_type="PermissionError",
                attempt=attempt,
            )
            return None
        except UnicodeDecodeError:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="deployment_id_peer_file_decode_error",
                error_type="UnicodeDecodeError",
                attempt=attempt,
            )
            return None
        except OSError as exc:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="deployment_id_peer_read",
                error_type=type(exc).__name__,
                attempt=attempt,
            )
            return None

        if not stored:
            # Peer is mid-write. Sleep briefly and retry.
            time.sleep(_PEER_READ_RETRY_DELAY_SECONDS)
            continue
        try:
            uuid.UUID(stored)
        except ValueError:
            # Peer wrote partial UUID. Sleep briefly and retry; the
            # peer may finish before our next attempt.
            time.sleep(_PEER_READ_RETRY_DELAY_SECONDS)
            continue
        return stored

    logger.warning(
        TELEMETRY_REPORT_FAILED,
        detail="deployment_id_peer_read_exhausted",
        attempts=_PEER_READ_RETRY_ATTEMPTS,
        using_generated_id=True,
    )
    return None


def _get_version() -> str:
    try:
        import synthorg  # noqa: PLC0415
    except ImportError:
        return "unknown"

    try:
        return synthorg.__version__
    except AttributeError:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="version_attribute_missing",
        )
        return "unknown"
