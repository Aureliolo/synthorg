"""Docker-based sandbox backend.

Executes commands inside Docker containers with workspace mount,
resource limits, network isolation, and timeout management.  Uses
``aiodocker`` for asynchronous Docker daemon communication.  The
keep-alive container + ``docker exec`` execution and lifecycle-strategy
dispatch live in :class:`DockerSandboxExecMixin`.
"""

import asyncio
import platform
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final

import aiodocker
import structlog.contextvars

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docker import (
    DOCKER_DAEMON_UNAVAILABLE,
    DOCKER_EXECUTE_FAILED,
    DOCKER_EXECUTE_START,
)
from synthorg.observability.events.sandbox import (
    SANDBOX_CONTAINER_TRACK_FAILED,
    SANDBOX_CONTAINER_UNTRACK_FAILED,
    SANDBOX_RUNTIME_RESOLVER_ATTACHED,
)
from synthorg.tools.sandbox.container_log_shipper import build_correlation_env
from synthorg.tools.sandbox.credential_manager import SandboxCredentialManager
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox_exec import DockerSandboxExecMixin
from synthorg.tools.sandbox.docker_sandbox_lifecycle import (
    DockerSandboxLifecycleMixin,
)
from synthorg.tools.sandbox.docker_sandbox_sidecar import DockerSandboxSidecarMixin
from synthorg.tools.sandbox.errors import SandboxError, SandboxStartError
from synthorg.tools.sandbox.lifecycle.per_call import PerCallStrategy

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from synthorg.observability.config import ContainerLogShippingConfig
    from synthorg.persistence.tracked_container_protocol import (
        TrackedContainerRepository,
    )
    from synthorg.tools.sandbox.lifecycle.protocol import (
        ContainerHandle,
        SandboxLifecycleStrategy,
    )
    from synthorg.tools.sandbox.result import SandboxResult
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


class DockerSandbox(
    DockerSandboxExecMixin,
    DockerSandboxSidecarMixin,
    DockerSandboxLifecycleMixin,
):
    """Docker sandbox backend.

    Runs commands in Docker containers with workspace mounts, resource
    limits (memory, CPU), network isolation, and timeout management.
    Container creation, exec, lifecycle dispatch, and teardown are
    provided by :class:`DockerSandboxExecMixin`.

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
                SANDBOX_CONTAINER_TRACK_FAILED,
                container_id=container_id[:12],
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
                SANDBOX_CONTAINER_UNTRACK_FAILED,
                container_id=container_id[:12],
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

    def _merged_env_list(
        self,
        env_overrides: Mapping[str, str] | None,
    ) -> list[str]:
        """Sanitise + validate env and overlay correlation IDs.

        Correlation IDs win over user-supplied duplicates.

        Args:
            env_overrides: User-supplied environment, or ``None``.

        Returns:
            ``KEY=VALUE`` entries for the container ``Env``.
        """
        sanitized = (
            self._credential_manager.sanitize_env(env_overrides)
            if env_overrides
            else None
        )
        merged: dict[str, str] = {}
        for entry in self._validate_env(sanitized):
            key, _, value = entry.partition("=")
            merged[key] = value
        for entry in build_correlation_env():
            key, _, value = entry.partition("=")
            merged[key] = value
        return [f"{k}={v}" for k, v in merged.items()]

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
                enforcement is active.
            owner_id: Lifecycle owner for container labeling.

        Returns:
            A dict suitable for ``aiodocker`` container creation.
        """
        env_list = self._merged_env_list(env_overrides)
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
        return {
            "Image": self._config.image,
            "Cmd": [command, *args],
            "WorkingDir": container_cwd,
            "Env": env_list,
            "Labels": labels,
            "HostConfig": host_config,
            "AttachStdout": True,
            "AttachStderr": True,
        }

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

    async def _acquire_owner_handle(
        self,
        *,
        owner_key: str,
        strategy_owns: bool,
        create_fn: Callable[[], Awaitable[ContainerHandle]],
    ) -> ContainerHandle:
        """Acquire a container for *owner_key* per the strategy.

        Reuse strategies with a stable owner and per-call both route
        through ``strategy.acquire`` (per-call's acquire just wraps
        ``create_fn`` and emits the per-call lifecycle events).  Degraded
        reuse (a reuse strategy configured but no stable owner) bypasses
        the strategy entirely so no dangling owner entry leaks.
        """
        strategy = self._lifecycle_strategy
        if strategy_owns or not strategy.reuses_container:
            return await strategy.acquire(
                owner_id=owner_key,
                create_fn=create_fn,
            )
        return await create_fn()

    async def _teardown_unowned(
        self,
        owner_key: str,
        handle: ContainerHandle,
    ) -> None:
        """Destroy a container the strategy does not own (per-call/degraded)."""
        strategy = self._lifecycle_strategy
        if not strategy.reuses_container:
            await strategy.release(
                owner_id=owner_key,
                destroy_fn=self._destroy_handle,
            )
        await self._destroy_handle(handle)

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

        handle = await self._acquire_owner_handle(
            owner_key=owner_key,
            strategy_owns=strategy_owns,
            create_fn=_create,
        )

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
                await self._teardown_unowned(owner_key, handle)

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
