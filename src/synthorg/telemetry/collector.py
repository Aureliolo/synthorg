"""Telemetry collector -- gathers curated metrics from runtime."""

import asyncio
import contextlib
import os
import platform
import sys
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.telemetry import (
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
)
from synthorg.telemetry.config import DEFAULT_ENVIRONMENT, MAX_STRING_LENGTH
from synthorg.telemetry.host_info import DockerHostInfo, fetch_docker_info
from synthorg.telemetry.privacy import PrivacyScrubber, PrivacyViolationError
from synthorg.telemetry.protocol import TelemetryEvent, TelemetryReporter
from synthorg.telemetry.reporters import create_reporter
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

        Applies the ``SYNTHORG_TELEMETRY`` opt-in override first, then
        runs the parsed ``config.environment`` through the four-level
        resolution chain in :func:`_resolve_environment`. The
        constructor performs **zero filesystem I/O**: loading or
        creating the anonymous ``deployment_id`` is deferred to
        :meth:`start` so the load runs through ``asyncio.to_thread``
        rather than blocking the event loop's thread (#1600). A
        disabled collector still leaves no on-disk trace.

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
        # Env var overrides config file (documented priority).
        env_val = os.environ.get("SYNTHORG_TELEMETRY", "").strip().lower()
        if env_val in ("true", "1", "yes"):
            config = config.model_copy(update={"enabled": True})
        elif env_val in ("false", "0", "no"):
            config = config.model_copy(update={"enabled": False})
        elif env_val:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="invalid_env_value",
                error_code="SYNTHORG_TELEMETRY_INVALID",
            )

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
        self._lifecycle_lock = asyncio.Lock()

        if not config.enabled:
            logger.debug(TELEMETRY_DISABLED)

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

    async def start(self) -> None:
        """Load the deployment ID and start the periodic heartbeat.

        Idempotent and safe under concurrent callers: serialised by
        a lifecycle lock so the deployment-id load, the startup
        event, and heartbeat task creation happen atomically. The
        load itself runs through ``asyncio.to_thread`` so the event
        loop's thread is never blocked on filesystem I/O (#1600).
        """
        async with self._lifecycle_lock:
            if not self._config.enabled:
                return
            if self._heartbeat_task is not None and not self._heartbeat_task.done():
                return
            if self._deployment_id is None:
                self._deployment_id = await self._load_or_create_deployment_id()
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
            # the new lifecycle, so guard explicitly.
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
                            exc_info=True,
                        )

                try:
                    await self.send_session_summary(params)
                except Exception as exc:
                    logger.warning(
                        TELEMETRY_REPORT_FAILED,
                        detail="send_session_summary_failed",
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )

                try:
                    await self._send_shutdown_event()
                except Exception as exc:
                    logger.warning(
                        TELEMETRY_REPORT_FAILED,
                        detail="send_shutdown_event_failed",
                        error_type=type(exc).__name__,
                        exc_info=True,
                    )

            try:
                await self._reporter.shutdown()
            except Exception as exc:
                logger.warning(
                    TELEMETRY_REPORT_FAILED,
                    detail="reporter_shutdown_failed",
                    error_type=type(exc).__name__,
                    exc_info=True,
                )

    async def send_heartbeat(
        self,
        params: _HeartbeatParams | None = None,
    ) -> None:
        """Send a heartbeat event with current deployment metrics."""
        if not self._config.enabled:
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
        """Send a session summary event with aggregate metrics."""
        if not self._config.enabled:
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

    async def _load_or_create_deployment_id(self) -> str:
        """Load deployment ID from file or create a new UUID (async).

        Offloads the entire path-validation + filesystem sequence to
        ``asyncio.to_thread`` so the event loop's thread never blocks
        on disk (#1600). Single ``to_thread`` boundary keeps the
        read + atomic-create + peer-recover sequence on one OS
        thread so the ``O_CREAT|O_EXCL`` race semantics are
        preserved end-to-end; splitting the helper across multiple
        ``to_thread`` calls would let two coroutines interleave
        between read and create.

        Returns a valid UUID string in all cases (never raises).
        Logs warnings on I/O errors.
        """
        return await asyncio.to_thread(
            _load_or_create_deployment_id_sync, self._data_dir
        )


def _load_or_create_deployment_id_sync(data_dir: Path) -> str:  # noqa: C901, PLR0912
    """Synchronous path-validate + load + atomic-create + peer-recover.

    Runs inside ``asyncio.to_thread`` so the event loop's thread is
    never blocked on filesystem I/O (#1600). The full sequence
    (path validation -> existence probe -> read -> ``O_CREAT|O_EXCL``
    create -> peer re-read on race) lives in one helper so the
    OS-level race semantics are preserved end-to-end; splitting the
    helper across multiple ``to_thread`` calls would let two
    coroutines interleave between read and create.

    Applies the OWASP path-injection recipe (``os.path.normpath`` +
    :py:meth:`str.startswith` on the normalised full path +
    trusted-root allow-list) immediately before the filesystem
    operations. The duplicate of
    :func:`synthorg.api.app._resolve_memory_dir` is deliberate
    defense-in-depth: ``normpath`` collapses ``..``/redundant
    separators that a caller constructing ``TelemetryCollector``
    directly could otherwise smuggle past ``data_dir``, and the
    startswith check is the sanitiser CodeQL's ``py/path-injection``
    query tracks across the sinks below.
    """
    # Build the full target path as a normalised, case-folded
    # string: the ``str(os.path.normcase(os.path.normpath(
    # os.path.join(base, name))))`` recipe from OWASP / CodeQL.
    # ``normpath`` collapses ``..`` and redundant ``/`` so the
    # prefix check below cannot be bypassed with
    # ``/data/../etc/telemetry_id``; ``normcase`` lower-cases on
    # Windows (no-op on POSIX) so the comparison is
    # case-insensitive where the filesystem is. The ``PTH*`` ruff
    # lints (prefer ``Path``) are intentionally suppressed: CodeQL's
    # ``py/path-injection`` query only recognises string-based
    # ``normpath``/``startswith`` + ``os.path``/builtin I/O as a
    # sanitiser + sink pair; the equivalent ``Path`` methods leave
    # the sinks flagged even with a valid guard.
    id_path_str = os.path.normcase(
        os.path.normpath(
            os.path.join(  # noqa: PTH118
                os.fspath(data_dir),
                "telemetry_id",
            ),
        ),
    )
    data_root = os.path.normcase(os.path.normpath(str(Path("/data"))))
    try:
        tmp_root: str | None = os.path.normcase(
            os.path.normpath(str(Path(tempfile.gettempdir()))),
        )
    except OSError, RuntimeError:
        tmp_root = None
    # Require a strict descendant of a trusted root (``root + sep``).
    # Equality (``path == root``) is rejected because the caller
    # would still derive ``parent / "telemetry"`` above this
    # function, and a path equal to the root would escape one level
    # up (``/data`` -> ``/telemetry``). The checks here use
    # ``id_path_str`` directly (the same variable read at every sink
    # below) so CodeQL's dataflow query sees the sanitiser on the
    # exact value it tracks.
    if not (
        id_path_str.startswith(data_root + os.sep)
        or (tmp_root is not None and id_path_str.startswith(tmp_root + os.sep))
    ):
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="data_dir_not_trusted",
            value=id_path_str,
        )
        return str(uuid.uuid4())

    # Use the sanitised string with plain ``os`` / builtin I/O so
    # the sanitiser and each sink sit on adjacent lines: the
    # pattern CodeQL's static dataflow query matches on. The
    # inline PTH-rule suppressions below carry the same rationale
    # as the upstream sanitiser.
    try:
        if os.path.exists(id_path_str):  # noqa: PTH110
            with open(id_path_str, encoding="utf-8") as fh:  # noqa: PTH123
                stored = fh.read().strip()
            if stored:
                try:
                    uuid.UUID(stored)
                except ValueError:
                    logger.warning(
                        TELEMETRY_REPORT_FAILED,
                        detail="deployment_id_invalid",
                        error_type="ValueError",
                    )
                else:
                    logger.debug(
                        TELEMETRY_DEPLOYMENT_ID_LOADED,
                        deployment_id=stored,
                    )
                    return stored
    except OSError as exc:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="deployment_id_read",
            error_type=type(exc).__name__,
        )

    new_id = str(uuid.uuid4())
    try:
        os.makedirs(  # noqa: PTH103
            os.path.dirname(id_path_str),  # noqa: PTH120
            exist_ok=True,
        )
        # Atomic exclusive create: under concurrent startups (e.g.
        # two backend replicas mounting the same ``/data`` volume)
        # the prior ``exists`` + ``open("w")`` pair could overwrite
        # a peer's freshly-written UUID and leave each replica with
        # a different deployment ID. ``O_CREAT | O_EXCL`` with the
        # final mode bits set atomically wins-or-loses the race;
        # if a peer wrote first we re-read and reuse its UUID so
        # the persisted ID stays stable.
        fd = os.open(
            id_path_str,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(new_id)
        except BaseException:
            # ``fdopen`` owns the fd on success; close it
            # ourselves if construction raised.
            os.close(fd)
            raise
    except FileExistsError:
        # A peer wrote first. Re-read; fall back to our own
        # freshly-minted UUID if their file is unreadable or
        # corrupt (same contract as the read path above).
        try:
            with open(id_path_str, encoding="utf-8") as fh:  # noqa: PTH123
                stored = fh.read().strip()
            uuid.UUID(stored)
        except (OSError, ValueError) as exc:
            logger.warning(
                TELEMETRY_REPORT_FAILED,
                detail="deployment_id_peer_read",
                error_type=type(exc).__name__,
            )
        else:
            logger.debug(
                TELEMETRY_DEPLOYMENT_ID_LOADED,
                deployment_id=stored,
            )
            return stored
    except OSError as exc:
        logger.warning(
            TELEMETRY_REPORT_FAILED,
            detail="deployment_id_write",
            error_type=type(exc).__name__,
        )
    else:
        logger.debug(
            TELEMETRY_DEPLOYMENT_ID_CREATED,
            deployment_id=new_id,
        )
    return new_id


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
