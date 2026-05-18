"""Docker-based sandbox backend.

Executes commands inside ephemeral Docker containers with workspace
mount, resource limits, network isolation, and timeout management.
Uses ``aiodocker`` for asynchronous Docker daemon communication.
"""

import asyncio
import platform
import uuid
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final

import aiodocker
import structlog.contextvars

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docker import (
    DOCKER_CONTAINER_CREATED,
    DOCKER_DAEMON_UNAVAILABLE,
    DOCKER_EXECUTE_FAILED,
    DOCKER_EXECUTE_START,
    DOCKER_EXECUTE_TIMEOUT,
)
from synthorg.observability.events.sandbox import (
    SANDBOX_CONTAINER_LOGS_COLLECTED,
    SANDBOX_LIFECYCLE_DISPATCH,
    SANDBOX_LIFECYCLE_OWNER_DEGRADED,
    SANDBOX_LIFECYCLE_RELEASE,
    SANDBOX_RUNTIME_RESOLVER_ATTACHED,
    SANDBOX_SIDECAR_REMOVE_FAILED,
    SANDBOX_SIDECAR_REMOVED,
    SANDBOX_SIDECAR_STARTED,
)
from synthorg.tools.sandbox.container_log_shipper import (
    build_correlation_env,
    collect_sidecar_logs,
    ship_container_logs,
)
from synthorg.tools.sandbox.credential_manager import SandboxCredentialManager
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox_lifecycle import (
    DockerSandboxLifecycleMixin,
)
from synthorg.tools.sandbox.docker_sandbox_sidecar import DockerSandboxSidecarMixin
from synthorg.tools.sandbox.errors import SandboxError, SandboxStartError
from synthorg.tools.sandbox.lifecycle.config import (
    STRATEGY_PER_AGENT,
    STRATEGY_PER_TASK,
)
from synthorg.tools.sandbox.lifecycle.per_call import PerCallStrategy
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle
from synthorg.tools.sandbox.result import SandboxResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.observability.config import ContainerLogShippingConfig
    from synthorg.persistence.tracked_container_protocol import (
        TrackedContainerRepository,
    )
    from synthorg.tools.sandbox.lifecycle.protocol import (
        SandboxLifecycleStrategy,
    )
    from synthorg.tools.sandbox.runtime_resolver import SandboxRuntimeResolver

_RESERVED_ENV_KEYS: Final[frozenset[str]] = frozenset(
    {
        "SIDECAR_ALLOWED_HOSTS",
        "SIDECAR_DNS_ALLOWED",
        "SIDECAR_LOOPBACK_ALLOWED",
        "SIDECAR_ALLOW_ALL",
        "SIDECAR_ADMIN_TOKEN",
        "SANDBOX_ALLOWED_HOSTS",
        "SANDBOX_DNS_ALLOWED",
        "SANDBOX_LOOPBACK_ALLOWED",
    }
)

_SIDECAR_HEALTH_POLL_INTERVAL: Final[float] = 0.2
_SIDECAR_HEALTH_TIMEOUT: Final[float] = 15.0
_SIDECAR_MEMORY: Final[str] = "64m"
_SIDECAR_CPU: Final[float] = 0.5
_SIDECAR_PIDS: Final[int] = 32

logger = get_logger(__name__)

_DEFAULT_CONFIG = DockerSandboxConfig()
_NANO_CPUS_MULTIPLIER: Final[int] = 1_000_000_000
_CONTAINER_WORKSPACE: Final[str] = "/workspace"
_STOP_TIMEOUT_SECONDS: Final[int] = 5
_DRIVE_SEPARATOR_PARTS: Final[int] = 2
# Cap structured-log stderr captures so a stream of binary output from
# inside a container cannot blow up our logging pipeline.
_MAX_STDERR_LOG_CHARS: Final[int] = 200

# Idle entrypoint for the long-lived sandbox container.  Container reuse
# (per-agent / per-task) requires a container that stays up across many
# ``docker exec`` invocations rather than running one baked-in command to
# completion.  ``tail -f /dev/null`` is universally available (coreutils
# is in every base image; it never exits and consumes no CPU), so it is
# preferred over ``sleep infinity`` which depends on a sleep build that
# accepts ``infinity``.
_KEEPALIVE_COMMAND: Final[str] = "tail"
_KEEPALIVE_ARGS: Final[tuple[str, ...]] = ("-f", "/dev/null")

# aiodocker exec stream frame identifiers (non-TTY multiplexed stream).
_EXEC_STREAM_STDOUT: Final[int] = 1
_EXEC_STREAM_STDERR: Final[int] = 2


def _to_posix_bind_path(path: Path) -> str:
    r"""Convert a host path to POSIX format for Docker bind mounts.

    On Windows, converts ``C:\Users\foo`` to ``/c/Users/foo``
    for Docker Desktop compatibility.

    Args:
        path: Host filesystem path to convert.

    Returns:
        POSIX-formatted path string suitable for Docker bind mounts.
    """
    if platform.system() == "Windows":
        posix = PurePosixPath(path.as_posix())
        parts = str(posix).split(":", 1)
        if len(parts) == _DRIVE_SEPARATOR_PARTS:
            drive = parts[0].lstrip("/").lower()
            rest = parts[1]
            return f"/{drive}{rest}"
    return str(path)


class DockerSandbox(DockerSandboxSidecarMixin, DockerSandboxLifecycleMixin):
    """Docker sandbox backend.

    Runs commands in ephemeral Docker containers with workspace mounts,
    resource limits (memory, CPU), network isolation, and timeout
    management.

    Attributes:
        config: Docker sandbox configuration.
        workspace: Absolute path to the workspace root directory.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        config: DockerSandboxConfig | None = None,
        workspace: Path,
        log_shipping_config: ContainerLogShippingConfig | None = None,
        clock: Clock | None = None,
        tracked_container_repo: TrackedContainerRepository | None = None,
        lifecycle_strategy: SandboxLifecycleStrategy | None = None,
    ) -> None:
        """Initialize the Docker sandbox.

        Args:
            config: Docker sandbox configuration (defaults to standard).
            workspace: Absolute path to the workspace root. Must exist.
            log_shipping_config: Container log shipping configuration.
                Default-constructed if not provided.
            clock: Time source for execution-duration measurements.
                Defaults to ``SystemClock()``; tests inject ``FakeClock``
                to drive elapsed-ms assertions deterministically.
            lifecycle_strategy: Container lifecycle strategy governing
                reuse and teardown. Defaults to ``PerCallStrategy`` (the
                ephemeral per-call behaviour) when omitted so direct
                construction stays per-call; the boot path injects the
                config-selected strategy via the sandbox factory.
            tracked_container_repo: Optional persistence handle. When
                provided, every mutation of ``_tracked_containers`` is
                mirrored to the backing store so a crashed process can
                reconcile orphaned containers on restart. When omitted
                (tests, ad-hoc instantiation), the in-memory dict is
                still authoritative and reconciliation is skipped.

        Raises:
            ValueError: If *workspace* is not absolute or does not exist.
        """
        if not workspace.is_absolute():
            msg = f"workspace must be an absolute path, got: {workspace}"
            logger.warning(DOCKER_EXECUTE_FAILED, error=msg)
            raise ValueError(msg)
        resolved = workspace.resolve()
        if not resolved.is_dir():
            msg = f"workspace directory does not exist: {resolved}"
            logger.warning(DOCKER_EXECUTE_FAILED, error=msg)
            raise ValueError(msg)
        self._config = config or _DEFAULT_CONFIG
        self._workspace = resolved
        self._docker: aiodocker.Docker | None = None
        self._tracked_containers: dict[str, str | None] = {}
        self._tracked_container_repo: TrackedContainerRepository | None = (
            tracked_container_repo
        )
        self._lock = asyncio.Lock()
        self._clock = clock or SystemClock()
        self._credential_manager = SandboxCredentialManager()
        self._lifecycle_strategy: SandboxLifecycleStrategy = (
            lifecycle_strategy if lifecycle_strategy is not None else PerCallStrategy()
        )
        self._runtime_resolver: SandboxRuntimeResolver | None = None
        if log_shipping_config is None:
            from synthorg.observability.config import (  # noqa: PLC0415
                ContainerLogShippingConfig as _Cfg,
            )

            log_shipping_config = _Cfg()
        self._log_shipping_config = log_shipping_config

    @property
    def config(self) -> DockerSandboxConfig:
        """Docker sandbox configuration."""
        return self._config

    async def _track_container(
        self,
        container_id: str,
        sidecar_id: str | None,
    ) -> None:
        """Track a container in-memory and (if configured) on disk.

        Mirrors the dict mutation to ``tracked_container_repo.save`` so a
        crashed process can reconcile orphans via the persisted record
        on restart. Persistence failures are logged and swallowed: the
        in-memory dict is authoritative for the current process and a
        DB blip must not block container creation.
        """
        self._tracked_containers[container_id] = sidecar_id
        repo = self._tracked_container_repo
        if repo is None or container_id.startswith("_sidecar:"):
            return
        try:
            from synthorg.persistence.tracked_container_protocol import (  # noqa: PLC0415
                TrackedContainerRecord,
            )

            await repo.save(
                TrackedContainerRecord(
                    container_id=container_id,
                    sidecar_id=sidecar_id,
                    created_at=self._clock.now(),
                ),
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                container_id=container_id[:12],
                reason="tracked_container_save_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _untrack_container(self, container_id: str) -> None:
        """Untrack a container in-memory and (if configured) on disk."""
        self._tracked_containers.pop(container_id, None)
        repo = self._tracked_container_repo
        if repo is None or container_id.startswith("_sidecar:"):
            return
        try:
            await repo.delete(container_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                container_id=container_id[:12],
                reason="tracked_container_delete_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    @property
    def workspace(self) -> Path:
        """Workspace root directory."""
        return self._workspace

    def set_runtime_resolver(
        self,
        resolver: SandboxRuntimeResolver,
    ) -> None:
        """Attach a runtime resolver for per-category runtime selection.

        Args:
            resolver: The resolver with probed runtime availability.
        """
        self._runtime_resolver = resolver
        logger.info(
            SANDBOX_RUNTIME_RESOLVER_ATTACHED,
            resolver_type=type(resolver).__name__,
        )

    async def _ensure_docker(self) -> aiodocker.Docker:
        """Lazily connect to the Docker daemon.

        Serialized with ``_lock`` to prevent duplicate client creation
        from concurrent calls.

        Returns:
            An ``aiodocker.Docker`` client instance.

        Raises:
            SandboxStartError: If the Docker daemon is unavailable.
        """
        async with self._lock:
            if self._docker is not None:
                return self._docker
            client = aiodocker.Docker()
            try:
                await client.version()
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                await client.close()
                logger.warning(
                    DOCKER_DAEMON_UNAVAILABLE,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = f"Docker daemon unavailable: {safe_error_description(exc)}"
                raise SandboxStartError(msg) from exc
            self._docker = client
            return client

    def _validate_cwd(self, cwd: Path) -> None:
        """Validate that *cwd* is within the workspace boundary.

        Args:
            cwd: Working directory to validate.

        Raises:
            SandboxError: If *cwd* is outside the workspace.
        """
        try:
            cwd.resolve().relative_to(self._workspace)
        except ValueError as exc:
            msg = f"Working directory '{cwd}' is outside workspace '{self._workspace}'"
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                error=msg,
                cwd=str(cwd),
                workspace=str(self._workspace),
            )
            raise SandboxError(msg) from exc

    def _resolve_cwd_in_container(self, cwd: Path | None) -> str:
        """Map a host cwd to a container-internal path.

        Args:
            cwd: Host working directory, or ``None`` for workspace root.

        Returns:
            POSIX path inside the container.
        """
        if cwd is None:
            return _CONTAINER_WORKSPACE
        rel = cwd.resolve().relative_to(self._workspace)
        return str(PurePosixPath(_CONTAINER_WORKSPACE) / rel)

    def _build_container_config(  # noqa: PLR0913
        self,
        *,
        command: str,
        args: tuple[str, ...],
        container_cwd: str,
        env_overrides: Mapping[str, str] | None,
        category: str = "",
        network_mode: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        """Build the Docker container creation config.

        Args:
            command: Executable name or path.
            args: Command arguments.
            container_cwd: Working directory inside the container.
            env_overrides: Environment variables for the container.
            category: Tool category for runtime resolution.
            network_mode: Override the default network mode. Used to
                set ``container:<sidecar_id>`` when sidecar
            owner_id: Lifecycle owner for container labeling.
                enforcement is active.

        Returns:
            A dict suitable for ``aiodocker`` container creation.
        """
        sanitized = (
            self._credential_manager.sanitize_env(env_overrides)
            if env_overrides
            else None
        )
        env_list = self._validate_env(sanitized)
        correlation_env = build_correlation_env()
        # Merge: correlation IDs override user-supplied duplicates.
        merged: dict[str, str] = {}
        for entry in env_list:
            key, _, value = entry.partition("=")
            merged[key] = value
        for entry in correlation_env:
            key, _, value = entry.partition("=")
            merged[key] = value
        env_list = [f"{k}={v}" for k, v in merged.items()]
        host_config = self._build_host_config(category=category)
        if network_mode is not None:
            host_config["NetworkMode"] = network_mode
        # WP-1: ``synthorg.managed=true`` is the canonical label the
        # reconciliation pass filters on at sandbox-subsystem start
        # (see ``synthorg.tools.sandbox.reconciliation``).  Operators
        # MUST NOT strip this label or the orphan cleanup will treat
        # the container as a foreign daemon process and leave it
        # running on restart.
        labels: dict[str, str] = {
            "synthorg.sandbox": "true",
            "synthorg.managed": "true",
        }
        if owner_id is not None:
            labels["synthorg.sandbox.owner_id"] = owner_id
        container_config: dict[str, Any] = {
            "Image": self._config.image,
            "Cmd": [command, *args],
            "WorkingDir": container_cwd,
            "Env": env_list,
            "Labels": labels,
            "HostConfig": host_config,
            "AttachStdout": True,
            "AttachStderr": True,
        }
        return container_config

    def _validate_env(
        self,
        env_overrides: Mapping[str, str] | None,
    ) -> list[str]:
        """Validate env_overrides and return the env list."""
        if env_overrides:
            conflicting = sorted(
                set(env_overrides) & _RESERVED_ENV_KEYS,
            )
            if conflicting:
                msg = (
                    "env_overrides cannot set reserved sandbox "
                    f"control variables: {conflicting}"
                )
                logger.warning(
                    DOCKER_EXECUTE_FAILED,
                    error=msg,
                    conflicting_keys=conflicting,
                )
                raise SandboxError(msg)
        return [f"{k}={v}" for k, v in (env_overrides or {}).items()]

    def _build_host_config(
        self,
        *,
        category: str = "",
    ) -> dict[str, Any]:
        """Build the Docker host config dict."""
        bind_path = _to_posix_bind_path(self._workspace)
        mount_mode = self._config.mount_mode
        bind_str = f"{bind_path}:{_CONTAINER_WORKSPACE}:{mount_mode}"
        memory_bytes = self._parse_memory_limit(
            self._config.memory_limit,
        )
        nano_cpus = int(self._config.cpu_limit * _NANO_CPUS_MULTIPLIER)
        tmpfs_spec = f"size={self._config.tmpfs_size},noexec,nosuid"
        host_config: dict[str, Any] = {
            "Binds": [bind_str],
            "Tmpfs": {"/tmp": tmpfs_spec},  # noqa: S108
            "Memory": memory_bytes,
            "NanoCpus": nano_cpus,
            "NetworkMode": self._config.network,
            "AutoRemove": False,
            "PidsLimit": self._config.pids_limit,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
        }
        runtime = self._resolve_runtime(category)
        if runtime is not None:
            host_config["Runtime"] = runtime
        return host_config

    def _resolve_runtime(self, category: str) -> str | None:
        """Resolve the effective container runtime for a category.

        Delegates to the ``SandboxRuntimeResolver`` when available,
        otherwise falls back to ``config.runtime``.
        """
        if self._runtime_resolver is not None:
            return self._runtime_resolver.resolve_runtime(category)
        return self._config.runtime

    def _needs_sidecar(self) -> bool:
        """Return ``True`` if sidecar-based network enforcement is needed.

        Enforcement activates when ``allowed_hosts`` is non-empty (or
        ``network_allow_all`` is set) and the default network is not
        ``"none"``.
        """
        has_rules = bool(
            self._config.allowed_hosts or self._config.network_allow_all,
        )
        return has_rules and self._config.network != "none"

    @staticmethod
    def _parse_memory_limit(limit: str) -> int:
        """Parse a Docker memory limit string to bytes.

        Supports suffixes ``k``, ``m``, ``g`` (case-insensitive).

        Args:
            limit: Memory limit string (e.g. ``"512m"``).

        Returns:
            Memory limit in bytes.

        Raises:
            ValueError: If the format is invalid.
        """
        limit_lower = normalize_ascii_lowercase(limit)
        if not limit_lower:
            msg = "Memory limit must not be empty"
            raise ValueError(msg)
        multipliers = {"k": 1024, "m": 1024**2, "g": 1024**3}
        if limit_lower[-1] in multipliers:
            result = int(limit_lower[:-1]) * multipliers[limit_lower[-1]]
        else:
            result = int(limit_lower)
        if result <= 0:
            msg = f"Memory limit must be positive, got: {limit!r}"
            raise ValueError(msg)
        return result

    @property
    def lifecycle_strategy(self) -> SandboxLifecycleStrategy:
        """The container lifecycle strategy in effect for this backend."""
        return self._lifecycle_strategy

    async def execute(  # noqa: PLR0913
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        category: str = "",
        owner_id: str | None = None,
    ) -> SandboxResult:
        """Execute a command inside a Docker container.

        Acquires a container for the resolved lifecycle owner via the
        configured :class:`SandboxLifecycleStrategy`, runs *command* in
        it via ``docker exec``, then either destroys the container
        (per-call / degraded) or leaves it for the strategy to tear
        down (per-agent grace, per-task release, shutdown cleanup).

        Args:
            command: Executable name or path.
            args: Command arguments.
            cwd: Working directory (defaults to workspace root).
            env_overrides: Extra env vars (only these -- no host leakage).
            timeout: Seconds before the command is killed. Clamped
                to ``config.timeout_seconds`` if larger.
            category: Tool category for per-category runtime selection.
            owner_id: Lifecycle owner (agent ID, task ID, or ``None``).
                When ``None`` and a reuse strategy is configured the
                owner is derived from the structlog correlation context
                (``agent_id`` for per-agent, ``task_id`` for per-task);
                if neither is available the call degrades to ephemeral
                per-call semantics.

        Returns:
            A ``SandboxResult`` with captured output and exit status.

        Raises:
            SandboxStartError: If the Docker daemon or image is unavailable.
            SandboxError: If cwd is outside the workspace boundary or
                *env_overrides* set reserved sandbox control variables.
        """
        work_dir = cwd if cwd is not None else self._workspace
        self._validate_cwd(work_dir)

        effective_timeout = min(
            timeout if timeout is not None else self._config.timeout_seconds,
            self._config.timeout_seconds,
        )
        container_cwd = self._resolve_cwd_in_container(cwd)

        # Validate / resolve the per-command env BEFORE any container
        # work so a reserved-variable rejection never leaks a container.
        exec_env = self._resolve_exec_env(env_overrides)

        owner_key, strategy_owns = self._resolve_lifecycle(owner_id)

        logger.debug(
            DOCKER_EXECUTE_START,
            command=command,
            args=args,
            cwd=container_cwd,
            timeout=effective_timeout,
            image=self._config.image,
            owner_id=owner_key,
        )

        docker = await self._ensure_docker()

        async def _create() -> ContainerHandle:
            return await self._create_keepalive_handle(
                docker=docker,
                container_cwd=container_cwd,
                env_overrides=env_overrides,
                category=category,
                owner_label=owner_key,
            )

        strategy = self._lifecycle_strategy
        if strategy_owns or not strategy.reuses_container:
            # Route through the strategy: reuse strategies with a stable
            # owner key, plus per-call (its acquire just wraps create_fn
            # and emits the per-call lifecycle events).
            handle = await strategy.acquire(
                owner_id=owner_key,
                create_fn=_create,
            )
        else:
            # Degraded reuse: a reuse strategy is configured but no
            # stable owner exists.  Bypass the strategy entirely so no
            # dangling owner entry leaks; behave ephemerally.
            handle = await _create()

        cfg = self._log_shipping_config
        sidecar_logs: tuple[dict[str, Any], ...] = ()
        result: SandboxResult | None = None
        try:
            result = await self._exec_command(
                docker=docker,
                handle=handle,
                command=command,
                args=args,
                container_cwd=container_cwd,
                exec_env=exec_env,
                timeout=effective_timeout,
            )
        finally:
            sidecar_logs = await self._collect_and_ship_logs(
                docker=docker,
                handle=handle,
                cfg=cfg,
                result=result,
            )
            if not strategy_owns:
                # per-call (via strategy) or degraded reuse (bypassed):
                # the backend owns teardown and destroys now.
                if not strategy.reuses_container:
                    await strategy.release(
                        owner_id=owner_key,
                        destroy_fn=self._destroy_handle,
                    )
                await self._destroy_handle(handle)

        # Unreachable when _exec_command raises -- the exception
        # propagates through the finally above.
        assert result is not None  # noqa: S101
        ctx = structlog.contextvars.get_contextvars()
        return result.model_copy(
            update={
                "sidecar_id": handle.sidecar_id,
                "sidecar_logs": sidecar_logs,
                "agent_id": ctx.get("agent_id"),
            },
        )

    def _resolve_lifecycle(self, owner_id: str | None) -> tuple[str, bool]:
        """Resolve the lifecycle owner key and teardown ownership.

        Returns a ``(owner_key, strategy_owns_teardown)`` pair.  An
        explicit *owner_id* always wins.  Otherwise, for a reuse
        strategy the key is derived from the structlog correlation
        context (``agent_id`` for per-agent, ``task_id`` for per-task);
        a per-call strategy gets a synthetic unique key.  When a reuse
        strategy is configured but no owner can be derived the call
        degrades to ephemeral per-call semantics (``strategy_owns`` is
        ``False`` so the backend destroys the container).

        Args:
            owner_id: Explicit lifecycle owner, or ``None``.

        Returns:
            ``(owner_key, strategy_owns_teardown)``.
        """
        strategy = self._lifecycle_strategy
        strategy_kind = self._config.lifecycle.strategy

        if owner_id is not None and owner_id.strip():
            key = owner_id.strip()
            owns = strategy.reuses_container
            logger.info(
                SANDBOX_LIFECYCLE_DISPATCH,
                strategy=strategy_kind,
                owner_id=key,
                owner_source="explicit",
                strategy_owns=owns,
            )
            return key, owns

        if not strategy.reuses_container:
            return f"per-call:{uuid.uuid4()}", False

        ctx = structlog.contextvars.get_contextvars()
        ctx_key: object | None = None
        if strategy_kind == STRATEGY_PER_AGENT:
            ctx_key = ctx.get("agent_id")
        elif strategy_kind == STRATEGY_PER_TASK:
            ctx_key = ctx.get("task_id")

        if ctx_key:
            key = str(ctx_key)
            logger.info(
                SANDBOX_LIFECYCLE_DISPATCH,
                strategy=strategy_kind,
                owner_id=key,
                owner_source="correlation_context",
                strategy_owns=True,
            )
            return key, True

        logger.warning(
            SANDBOX_LIFECYCLE_OWNER_DEGRADED,
            strategy=strategy_kind,
            reason=(
                "no explicit owner_id and no correlation context; "
                "container will not be reused (ephemeral per-call)"
            ),
        )
        return f"per-call:{uuid.uuid4()}", False

    async def release_owner(self, owner_id: str) -> None:
        """Signal that *owner_id* no longer needs its sandbox container.

        Dispatches to the configured lifecycle strategy's ``release``.
        For per-agent this starts the grace timer; for per-task it
        destroys the container immediately; for per-call it is a no-op.
        Wired at the owner boundary (task completion / agent stop).

        Args:
            owner_id: The same identifier passed as ``owner_id`` to
                ``execute`` (agent ID for per-agent, task ID for
                per-task).
        """
        if not owner_id or not owner_id.strip():
            return
        key = owner_id.strip()
        logger.info(
            SANDBOX_LIFECYCLE_RELEASE,
            strategy=self._config.lifecycle.strategy,
            owner_id=key,
            action="release_owner",
        )
        await self._lifecycle_strategy.release(
            owner_id=key,
            destroy_fn=self._destroy_handle,
        )

    async def _cleanup_failed_sidecar(
        self,
        docker: aiodocker.Docker,
        sidecar_id: str,
    ) -> None:
        """Remove a sidecar that failed to start; untrack its temp key."""
        removed = await self._remove_container(docker, sidecar_id)
        if removed:
            await self._untrack_container(f"_sidecar:{sidecar_id}")

    async def _bring_up_sidecar(self, docker: aiodocker.Docker) -> str:
        """Create, start, and health-check the network sidecar.

        Args:
            docker: Docker client.

        Returns:
            The started, healthy sidecar container ID.

        Raises:
            SandboxStartError: If sidecar startup or health-check fails.
        """
        sidecar_id = await self._create_sidecar(docker)
        await self._track_container(f"_sidecar:{sidecar_id}", None)
        try:
            sidecar_obj = docker.containers.container(sidecar_id)  # pyright: ignore[reportAttributeAccessIssue]
            await sidecar_obj.start()
            logger.debug(
                SANDBOX_SIDECAR_STARTED,
                sidecar_id=sidecar_id[:12],
            )
            await self._wait_sidecar_healthy(docker, sidecar_id)
        except MemoryError, RecursionError:
            await self._cleanup_failed_sidecar(docker, sidecar_id)
            raise
        except Exception as exc:
            await self._cleanup_failed_sidecar(docker, sidecar_id)
            msg = f"Sidecar startup failed: {safe_error_description(exc)}"
            raise SandboxStartError(msg) from exc
        except BaseException:
            await self._cleanup_failed_sidecar(docker, sidecar_id)
            raise
        return sidecar_id

    async def _create_keepalive_handle(
        self,
        *,
        docker: aiodocker.Docker,
        container_cwd: str,
        env_overrides: Mapping[str, str] | None,
        category: str,
        owner_label: str,
    ) -> ContainerHandle:
        """Create and start a long-lived idle sandbox container.

        The container runs an idle entrypoint so it survives across
        many ``docker exec`` calls (the reuse model).  When network
        enforcement is active a paired sidecar is created, started, and
        health-checked first; the two share a lifetime.

        Args:
            docker: Docker client.
            container_cwd: Container working directory.
            env_overrides: Environment baked into the container.
            category: Tool category for runtime resolution.
            owner_label: Lifecycle owner recorded as a container label.

        Returns:
            A ``ContainerHandle`` for a started, idle container.

        Raises:
            SandboxStartError: If sidecar/container creation or start
                fails.
        """
        sidecar_id: str | None = None
        network_mode: str | None = None
        if self._needs_sidecar():
            sidecar_id = await self._bring_up_sidecar(docker)
            network_mode = f"container:{sidecar_id}"

        config = self._build_container_config(
            command=_KEEPALIVE_COMMAND,
            args=_KEEPALIVE_ARGS,
            container_cwd=container_cwd,
            env_overrides=env_overrides,
            category=category,
            network_mode=network_mode,
            owner_id=owner_label,
        )

        try:
            container = await docker.containers.create(config)  # pyright: ignore[reportAttributeAccessIssue]
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            if sidecar_id:
                await self._cleanup_failed_sidecar(docker, sidecar_id)
            error_desc = safe_error_description(exc)
            msg = f"Failed to create container: {error_desc}"
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                error_type=type(exc).__name__,
                error=error_desc,
            )
            raise SandboxStartError(msg) from exc

        container_id = container.id
        await self._track_container(container_id, sidecar_id)
        if sidecar_id:
            await self._untrack_container(f"_sidecar:{sidecar_id}")
        logger.debug(
            DOCKER_CONTAINER_CREATED,
            container_id=container_id[:12],
            image=self._config.image,
        )

        container_obj = docker.containers.container(container_id)  # pyright: ignore[reportAttributeAccessIssue]
        try:
            await container_obj.start()
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            if sidecar_id:
                await self._remove_container(docker, sidecar_id)
            await self._remove_container(docker, container_id)
            await self._untrack_container(container_id)
            error_desc = safe_error_description(exc)
            msg = f"Failed to start container {container_id[:12]}: {error_desc}"
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=error_desc,
            )
            raise SandboxStartError(msg) from exc

        return ContainerHandle(
            container_id=container_id,
            sidecar_id=sidecar_id,
            network_mode=network_mode or self._config.network,
        )

    async def _exec_command(  # noqa: PLR0913
        self,
        *,
        docker: aiodocker.Docker,
        handle: ContainerHandle,
        command: str,
        args: tuple[str, ...],
        container_cwd: str,
        exec_env: dict[str, str],
        timeout: float,  # noqa: ASYNC109
    ) -> SandboxResult:
        """Run *command* inside an already-running container via exec.

        Args:
            docker: Docker client.
            handle: Handle of the started keep-alive container.
            command: Executable name or path.
            args: Command arguments.
            container_cwd: Working directory inside the container.
            exec_env: Resolved, validated environment for the command.
            timeout: Seconds before the command is killed.

        Returns:
            A ``SandboxResult`` with captured output and exit status.

        Raises:
            SandboxStartError: If the exec instance cannot be created.
        """
        container_id = handle.container_id
        container_obj = docker.containers.container(container_id)  # pyright: ignore[reportAttributeAccessIssue]
        try:
            exec_obj = await container_obj.exec(
                cmd=[command, *args],
                stdout=True,
                stderr=True,
                stdin=False,
                tty=False,
                environment=exec_env,
                workdir=container_cwd,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            error_desc = safe_error_description(exc)
            msg = (
                f"Failed to exec command in container {container_id[:12]}: {error_desc}"
            )
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=error_desc,
            )
            raise SandboxStartError(msg) from exc

        start_mono = self._clock.monotonic()
        stream = exec_obj.start(detach=False)
        timed_out = False
        stdout = ""
        stderr = ""
        try:
            stdout, stderr = await asyncio.wait_for(
                self._collect_exec_output(stream),
                timeout=timeout,
            )
        except TimeoutError:
            timed_out = True
            logger.warning(
                DOCKER_EXECUTE_TIMEOUT,
                container_id=container_id[:12],
                timeout=timeout,
            )
            # The runaway process shares the container; stopping it
            # kills the exec.  Reuse strategies treat the container as
            # compromised: the next release/cleanup tears it down, and
            # a re-acquire within the grace window that hits a stopped
            # container surfaces as an error result, never a crash.
            await self._stop_container(docker, container_id)
        finally:
            await self._safe_close_stream(stream)

        elapsed_ms = int((self._clock.monotonic() - start_mono) * 1000)

        if timed_out:
            returncode = -1
        else:
            returncode = await self._exec_returncode(exec_obj, container_id)

        self._log_execution_outcome(
            command,
            args,
            container_id,
            returncode,
            stderr,
        )

        if timed_out:
            return SandboxResult(
                stdout=stdout,
                stderr=stderr or f"Command timed out after {timeout}s",
                returncode=returncode,
                timed_out=True,
                container_id=container_id,
                execution_time_ms=elapsed_ms,
            )
        return SandboxResult(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            container_id=container_id,
            execution_time_ms=elapsed_ms,
        )

    @staticmethod
    async def _collect_exec_output(stream: Any) -> tuple[str, str]:
        """Drain an aiodocker exec stream into ``(stdout, stderr)``.

        Non-TTY exec streams multiplex frames tagged stdout (1) or
        stderr (2).  Frames are decoded UTF-8 with replacement so
        binary output never raises.

        Args:
            stream: The ``aiodocker`` exec ``Stream``.

        Returns:
            Decoded ``(stdout, stderr)``.
        """
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        # Bounded: drains until the exec stream hits EOF (read_out()
        # returns None when the exec'd command exits), and the sole
        # caller wraps this in asyncio.wait_for(timeout) so a hung
        # command is cancelled at the lifecycle-resolved timeout.
        # lint-allow: long-running-loop-kill-switch -- EOF + caller timeout.
        while True:
            message = await stream.read_out()
            if message is None:
                break
            raw = message.data
            text = (
                raw.decode("utf-8", "replace")
                if isinstance(raw, bytes | bytearray)
                else str(raw)
            )
            if message.stream == _EXEC_STREAM_STDERR:
                stderr_parts.append(text)
            elif message.stream == _EXEC_STREAM_STDOUT:
                stdout_parts.append(text)
            else:
                # TTY / unmultiplexed frame: treat as stdout.
                stdout_parts.append(text)
        return "".join(stdout_parts), "".join(stderr_parts)

    @staticmethod
    async def _safe_close_stream(stream: Any) -> None:
        """Close an exec stream, swallowing best-effort close errors."""
        try:
            await stream.close()
        except Exception as exc:
            logger.debug(
                DOCKER_EXECUTE_FAILED,
                reason="exec_stream_close_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    @staticmethod
    async def _exec_returncode(exec_obj: Any, container_id: str) -> int:
        """Return the exec exit code, or ``-1`` if it cannot be read."""
        try:
            info = await exec_obj.inspect()
        except Exception as exc:
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                container_id=container_id[:12],
                reason="exec_inspect_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return -1
        code = info.get("ExitCode")
        return code if isinstance(code, int) else -1

    def _resolve_exec_env(
        self,
        env_overrides: Mapping[str, str] | None,
    ) -> dict[str, str]:
        """Build the validated per-command environment for ``exec``.

        Mirrors the container-create env policy: credential sanitising,
        reserved-variable rejection, and correlation-ID overlay (the
        correlation IDs win over user-supplied duplicates).

        Args:
            env_overrides: User-supplied environment, or ``None``.

        Returns:
            Mapping passed verbatim to ``container.exec(environment=)``.

        Raises:
            SandboxError: If *env_overrides* set reserved variables.
        """
        sanitized = (
            self._credential_manager.sanitize_env(env_overrides)
            if env_overrides
            else None
        )
        env_list = self._validate_env(sanitized)
        merged: dict[str, str] = {}
        for entry in env_list:
            key, _, value = entry.partition("=")
            merged[key] = value
        for entry in build_correlation_env():
            key, _, value = entry.partition("=")
            merged[key] = value
        return merged

    async def _destroy_handle(self, handle: ContainerHandle) -> None:
        """Stop+remove a container and its sidecar; untrack both.

        Used as the ``destroy_fn`` for every lifecycle strategy and for
        the backend's own per-call teardown.  Best-effort and
        idempotent: a missing container is tolerated by
        ``_remove_container``.

        Args:
            handle: The container handle to destroy.
        """
        docker = self._docker
        if docker is None:
            return
        sandbox_removed = await self._remove_container(
            docker,
            handle.container_id,
        )
        if sandbox_removed:
            await self._untrack_container(handle.container_id)
        if handle.sidecar_id:
            sidecar_removed = await self._remove_container(
                docker,
                handle.sidecar_id,
            )
            if sidecar_removed:
                logger.debug(
                    SANDBOX_SIDECAR_REMOVED,
                    sidecar_id=handle.sidecar_id[:12],
                )
            else:
                logger.warning(
                    SANDBOX_SIDECAR_REMOVE_FAILED,
                    sidecar_id=handle.sidecar_id[:12],
                    error="removal failed, sidecar remains tracked",
                )

    async def _collect_and_ship_logs(
        self,
        *,
        docker: aiodocker.Docker,
        handle: ContainerHandle,
        cfg: ContainerLogShippingConfig,
        result: SandboxResult | None,
    ) -> tuple[dict[str, Any], ...]:
        """Collect sidecar logs and ship container logs (best-effort).

        Never raises ordinary errors -- log shipping must not mask the
        execution outcome or block teardown.

        Args:
            docker: Docker client.
            handle: The container handle just executed against.
            cfg: Log-shipping configuration.
            result: The execution result, or ``None`` if exec raised.

        Returns:
            Parsed sidecar log entries (empty when disabled or absent).
        """
        sidecar_logs: tuple[dict[str, Any], ...] = ()
        if handle.sidecar_id and cfg.enabled:
            try:
                sidecar_logs = await collect_sidecar_logs(
                    docker,
                    handle.sidecar_id,
                    config=cfg,
                )
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.debug(
                    SANDBOX_CONTAINER_LOGS_COLLECTED,
                    sidecar_id=handle.sidecar_id[:12],
                    status="collection_error_in_cleanup",
                )

        _stdout = result.stdout if result is not None else ""
        _stderr = result.stderr if result is not None else ""
        _ms = (result.execution_time_ms or 0) if result is not None else 0
        await ship_container_logs(
            config=cfg,
            container_id=handle.container_id,
            sidecar_id=handle.sidecar_id,
            stdout=_stdout,
            stderr=_stderr,
            sidecar_logs=sidecar_logs,
            execution_time_ms=_ms,
        )
        return sidecar_logs
