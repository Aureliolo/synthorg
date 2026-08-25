# module-kind: complex_service
"""Docker-based sandbox backend.

Executes commands inside Docker containers with workspace mount,
resource limits, network isolation, and timeout management.  Uses
``aiodocker`` for asynchronous Docker daemon communication.  The
keep-alive container + ``docker exec`` execution and lifecycle-strategy
dispatch live in :class:`DockerSandboxExecMixin`.

One cohesive responsibility: be the Docker-backed sandbox entry
point. The execute() pipeline (project subdir resolution -> env
policy validation -> container config -> lifecycle acquire ->
exec -> log ship -> teardown) is one path; the exec / sidecar /
lifecycle mixins already partition the implementation, so the
residual class is the composing entry that holds workspace-mount
security and the per-execution policy chokepoint.
"""

import asyncio
import fnmatch
import platform
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Final, override

import aiodocker
import structlog.contextvars
from pydantic import JsonValue

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.git_env import git_config_env
from synthorg.core.types import NotBlankStr
from synthorg.core.workspace_sharing import workspace_share_gid
from synthorg.engine.workspace.paths import PROJECTS_SUBDIR
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.config import ContainerLogShippingConfig
from synthorg.observability.events.docker import (
    DOCKER_DAEMON_UNAVAILABLE,
    DOCKER_EXECUTE_FAILED,
    DOCKER_EXECUTE_START,
)
from synthorg.observability.events.sandbox import (
    SANDBOX_CONTAINER_TRACK_FAILED,
    SANDBOX_CONTAINER_UNTRACK_FAILED,
    SANDBOX_ENV_FILTERED,
    SANDBOX_RUNTIME_RESOLVER_ATTACHED,
    SANDBOX_WORKSPACE_MOUNT_UNRESOLVED,
    SANDBOX_WORKSPACE_SHARING_UNAVAILABLE,
)
from synthorg.persistence.tracked_container_protocol import (
    TrackedContainerRepository,
)
from synthorg.tools.sandbox._container_limits import nano_cpus, parse_memory_limit
from synthorg.tools.sandbox._mount_mode import MountMode, resolve_mount_mode
from synthorg.tools.sandbox._mount_paths import CONTAINER_TMP, CONTAINER_WORKSPACE
from synthorg.tools.sandbox._sidecar_resolution import (
    get_resolved_docker_connect_timeout_seconds,
)
from synthorg.tools.sandbox.active_environment import get_active_sandbox_environment
from synthorg.tools.sandbox.credential_manager import SandboxCredentialManager
from synthorg.tools.sandbox.deployment_identity import (
    DEPLOYMENT_LABEL,
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
    deployment_id_for,
)
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.docker_sandbox_exec import DockerSandboxExecMixin
from synthorg.tools.sandbox.docker_sandbox_lifecycle import (
    DockerSandboxLifecycleMixin,
)
from synthorg.tools.sandbox.docker_sandbox_sidecar import DockerSandboxSidecarMixin
from synthorg.tools.sandbox.errors import (
    SandboxError,
    SandboxStartError,
    SandboxWorkspaceUnmappableError,
)
from synthorg.tools.sandbox.lifecycle.per_call import PerCallStrategy
from synthorg.tools.sandbox.lifecycle.protocol import (
    ContainerHandle,
    SandboxLifecycleStrategy,
)
from synthorg.tools.sandbox.result import SandboxResult
from synthorg.tools.sandbox.runtime_resolver import SandboxRuntimeResolver
from synthorg.tools.sandbox.workspace_mount import (
    WorkspaceMount,
    discover_own_container,
    resolve_workspace_mount,
)

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

logger = get_logger(__name__)

# Sticky world-writable, the /tmp convention: the container's user is not known
# here (it belongs to the image), so the mount cannot name a uid to own it.
_TMPFS_MODE: Final[str] = "1777"
_DRIVE_SEPARATOR_PARTS: Final[int] = 2

#: Home for the container's user, backed by tmpfs and reclaimed with the
#: container. The read-only root filesystem covers whatever home the image
#: ships, and git writes its configuration there: without a writable one the
#: remedy git itself prints for a cross-uid workspace ("git config --global
#: --add safe.directory") fails on a read-only filesystem, so an agent hits
#: two dead ends in a row before it can run a single version-control command.
#: Named here rather than taken from the image because the value has to reach
#: the environment as well as the mount, and an image that disagrees would
#: leave the two pointing at different directories.
SANDBOX_HOME: Final[str] = "/home/sandbox"


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

    def __init__(
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
        # Constructed here rather than held as a module-level default: the
        # image comes from a resolution cache that startup fills from
        # ``tools.sandbox_image`` (and so from the operator's digest pin), and
        # a singleton built at import time reads that cache before anything
        # has filled it. Every sandbox then ran on the version-tag fallback
        # for the life of the process, which on a dev build is a tag the
        # registry does not carry: every shell_command and code_runner call
        # answered "No such image", and no CodeExecutionRecord could exist.
        self._config = config if config is not None else DockerSandboxConfig()
        self._workspace = resolved
        self._docker: aiodocker.Docker | None = None
        self._tracked_containers: dict[str, str | None] = {}
        self._tracked_container_repo: TrackedContainerRepository | None = (
            tracked_container_repo
        )
        self._lock = asyncio.Lock()
        self._init_execution_leases(command_timeout=self._config.timeout_seconds)
        self._clock = clock or SystemClock()
        self._credential_manager = SandboxCredentialManager()
        self._lifecycle_strategy: SandboxLifecycleStrategy = (
            lifecycle_strategy if lifecycle_strategy is not None else PerCallStrategy()
        )
        self._runtime_resolver: SandboxRuntimeResolver | None = None
        # ``None`` means "this process's own paths are the daemon's", which is
        # true on the host and false in a container. Resolved once against the
        # live daemon rather than here, because construction is synchronous and
        # the answer belongs to the daemon.
        self._workspace_mount: WorkspaceMount | None = None
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

    @override
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SANDBOX_CONTAINER_TRACK_FAILED,
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    @override
    async def _untrack_container(self, container_id: str) -> None:
        """Untrack a container in-memory and (if configured) on disk."""
        self._tracked_containers.pop(container_id, None)
        repo = self._tracked_container_repo
        if repo is None or container_id.startswith("_sidecar:"):
            return
        try:
            await repo.delete(container_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
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

    @override
    async def _ensure_docker(self) -> aiodocker.Docker:
        """Lazily connect to the Docker daemon.

        Serialized with ``_lock`` to prevent duplicate client creation
        from concurrent calls.

        Returns:
            An ``aiodocker.Docker`` client instance.

        Raises:
            SandboxStartError: If the Docker daemon is unavailable.
            SandboxError: If the daemon is reachable but this process cannot
                describe its own workspace to it. Passed through rather than
                rewritten, because it names a deployment condition the generic
                "daemon unavailable" would hide.
        """
        async with self._lock:
            if self._docker is not None:
                return self._docker
            client = aiodocker.Docker()
            # The client is this block's to own until it is published on
            # ``self._docker``: nothing else can reach it, so any exit that
            # leaves it unpublished must also close it or the session and its
            # daemon socket leak once per attempt, forever.
            published = False
            try:
                # Bounds the CONNECT path only, never a running execution: a
                # sandboxed test suite legitimately runs for minutes, so a
                # client-wide ``total`` timeout would kill the work rather than
                # the wedge. Every tool call funnels through the same lock, so
                # an unbounded wait here takes down the whole tool plane rather
                # than one call. Resolved per connect, because a cold Docker
                # Desktop daemon outlasts the default and an operator who
                # cannot raise it has no sandbox-backed tools at all.
                async with asyncio.timeout(
                    get_resolved_docker_connect_timeout_seconds()
                ):
                    version = await client.version()
                    # Resolved before any container is created and inside the
                    # same lock, so a concurrent caller cannot build a host
                    # config against a mount that is not worked out yet.
                    self._workspace_mount = await self._resolve_workspace_mount(
                        client, version
                    )
                self._docker = client
                published = True
            except SandboxError:
                raise
            except Exception as exc:
                reraise_critical(exc)
                logger.warning(
                    DOCKER_DAEMON_UNAVAILABLE,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = f"Docker daemon unavailable: {safe_error_description(exc)}"
                raise SandboxStartError(msg) from exc
            finally:
                if not published:
                    await self._close_quietly(client)
            return client

    async def _close_quietly(self, client: aiodocker.Docker) -> None:
        """Close *client*, logging rather than raising when the close fails.

        Called from a ``finally`` on a path that is already failing, where a
        raise would replace the real cause with the cleanup's own.

        Args:
            client: The client to close.
        """
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DOCKER_DAEMON_UNAVAILABLE,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                phase="close",
            )

    async def _resolve_workspace_mount(
        self,
        client: aiodocker.Docker,
        version: Mapping[str, object],
    ) -> WorkspaceMount | None:
        """Work out how a sibling sandbox reaches this process's workspace.

        Args:
            client: The connected daemon client.
            version: What ``GET /version`` answered, carrying ``ApiVersion``.

        Returns:
            The mount to reproduce, or ``None`` when this process runs on the
            host and its own paths are already the daemon's.
        """
        api_version = version.get("ApiVersion")
        own = discover_own_container()
        return await resolve_workspace_mount(
            docker=client,
            root=self._workspace,
            api_version=api_version if isinstance(api_version, str) else "",
            container_id=own.container_id,
            certain=own.certain,
        )

    async def _project_root(self, project_id: str | None) -> Path:
        """Resolve the per-execution mount root for *project_id*.

        ``None`` mounts the whole workspace root (the no-project
        execution mode: empty-company backstop / non-project tooling).
        A set ``project_id`` mounts ``<workspace>/projects/<project_id>``
        so a project-A sandbox cannot see project-B files. The
        separator guard mirrors ``ProjectWorkspaceService`` so a crafted
        id cannot traverse out of the projects subdir.

        The resolved root is re-anchored under the resolved projects
        directory so a symlinked project entry cannot resolve to an
        arbitrary host path and mount it into ``/workspace``. Filesystem
        probes run in a worker thread to avoid blocking the event loop.

        Returns:
            Result of type ``Path``.

        Raises:
            SandboxError: ``project_id`` bears path separators, resolves
                outside the projects root (symlink escape), or names a
                project tree that does not exist on the volume.
        """
        if project_id is None:
            return self._workspace
        pid = str(project_id)
        if not pid.strip() or pid == "." or "/" in pid or "\\" in pid or ".." in pid:
            msg = f"refusing path-separator-bearing project_id {pid!r}"
            logger.warning(DOCKER_EXECUTE_FAILED, error=msg, project_id=pid)
            raise SandboxError(msg)
        projects_root = await asyncio.to_thread(
            (self._workspace / PROJECTS_SUBDIR).resolve
        )
        try:
            root = await asyncio.to_thread((projects_root / pid).resolve)
            exists = await asyncio.to_thread(root.is_dir)
        except (OSError, ValueError) as exc:
            # An oversized / invalid project_id can make resolve()/is_dir()
            # raise (e.g. ENAMETOOLONG) instead of returning; surface it as
            # a sandbox error rather than leaking a raw OSError.
            msg = f"project workspace does not exist for project {pid!r}"
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                error=msg,
                project_id=pid,
                error_type=type(exc).__name__,
                error_detail=safe_error_description(exc),
            )
            raise SandboxError(msg) from exc
        try:
            root.relative_to(projects_root)
        except ValueError as exc:
            msg = f"project workspace escapes projects root for project {pid!r}: {root}"
            logger.warning(DOCKER_EXECUTE_FAILED, error=msg, project_id=pid)
            raise SandboxError(msg) from exc
        if not exists:
            msg = f"project workspace does not exist for project {pid!r}: {root}"
            logger.warning(DOCKER_EXECUTE_FAILED, error=msg, project_id=pid)
            raise SandboxError(msg)
        return root

    @staticmethod
    def _rooted(cwd: Path, effective_root: Path) -> Path:
        """Anchor a caller-supplied *cwd* to the mount root when it is relative.

        A tool passes the working directory an agent named, and an agent names
        it relative to the project it is working in. Left relative, every later
        ``resolve()`` reads it against the service process's own directory,
        which is not the project and usually not even under the workspace.

        Returns:
            *cwd* unchanged when absolute, else *cwd* under *effective_root*.
        """
        return cwd if cwd.is_absolute() else effective_root / cwd

    def _validate_cwd(self, cwd: Path, effective_root: Path | None = None) -> None:
        """Validate that *cwd* is within *effective_root*.

        Args:
            cwd: Working directory to validate.
            effective_root: Per-execution mount root (project subdir or
                the whole workspace). Defaults to the workspace root.

        Raises:
            SandboxError: If *cwd* is outside *effective_root*.
        """
        effective_root = (
            effective_root if effective_root is not None else self._workspace
        )
        try:
            cwd.resolve().relative_to(effective_root)
        except ValueError as exc:
            msg = f"Working directory '{cwd}' is outside workspace '{effective_root}'"
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                error=msg,
                cwd=str(cwd),
                workspace=str(effective_root),
            )
            raise SandboxError(msg) from exc

    def _resolve_cwd_in_container(
        self,
        cwd: Path | None,
        effective_root: Path | None = None,
    ) -> str:
        """Map a host cwd to a container-internal path under the mount root.

        Args:
            cwd: Host working directory, or ``None`` for the mount root.
            effective_root: Per-execution mount root bound at ``/workspace``.
                Defaults to the workspace root.

        Returns:
            POSIX path inside the container.
        """
        if cwd is None:
            return CONTAINER_WORKSPACE
        effective_root = (
            effective_root if effective_root is not None else self._workspace
        )
        rel = cwd.resolve().relative_to(effective_root)
        return str(PurePosixPath(CONTAINER_WORKSPACE) / rel)

    def _matches_denylist(self, name: str) -> bool:
        """Check if an env var name matches any denylist pattern.

        Both name and patterns are uppercased for case-insensitive
        matching so the denylist catches secrets / loader-injection
        vars regardless of casing.

        Returns:
            ``True`` if the operation succeeds, ``False`` otherwise.
        """
        upper = name.upper()
        return any(
            fnmatch.fnmatch(upper, pat.upper())
            for pat in self._config.env_denylist_patterns
        )

    def _screen_declaration_env(
        self,
        env_additions: Mapping[str, str],
    ) -> dict[str, str]:
        """Drop denylisted keys from declaration-sourced env additions.

        The per-project environment declaration is committed code, but
        unlike trusted internal overrides it is screened through the
        secret / loader-injection denylist so a declared dangerous
        variable (e.g. ``LD_PRELOAD``, ``PYTHONPATH``) cannot hijack
        tool execution inside the container.  Dropped keys are logged.

        Returns:
            Mapping from ``str`` to ``str``.
        """
        screened: dict[str, str] = {}
        dropped: list[str] = []
        for name, value in env_additions.items():
            if self._matches_denylist(name):
                dropped.append(name)
            else:
                screened[name] = value
        if dropped:
            logger.warning(
                SANDBOX_ENV_FILTERED,
                source="declaration",
                dropped_count=len(dropped),
                dropped_keys=sorted(dropped),
            )
        return screened

    def _merged_env_list(
        self,
        env_overrides: Mapping[str, str] | None,
    ) -> list[str]:
        """Sanitise + validate env and overlay correlation IDs.

        Delegates to ``_resolve_exec_env`` (the single source of truth
        for the env policy: credential sanitising, reserved-variable
        rejection, correlation-ID overlay) and renders the merged
        mapping as ``KEY=VALUE`` entries for container creation.

        Correlation IDs win over user-supplied duplicates.

        Args:
            env_overrides: User-supplied environment, or ``None``.

        Returns:
            ``KEY=VALUE`` entries for the container ``Env``.
        """
        merged = self._resolve_exec_env(env_overrides)
        # Stated rather than left to the image, because the tmpfs that makes a
        # home writable is mounted at a path this module names: an image whose
        # own HOME points elsewhere would send git back to the read-only root
        # and the mount would be present with nothing using it.
        merged.setdefault("HOME", SANDBOX_HOME)
        # The workspace is owned by the backend's uid and reached through its
        # group, which is the whole point of the split, so git reads it as
        # someone else's repository and refuses every command with "detected
        # dubious ownership". The mount is what makes this tree ours, so the
        # exemption is scoped to exactly that path.
        for key, value in git_config_env(
            {"safe.directory": CONTAINER_WORKSPACE}
        ).items():
            merged.setdefault(key, value)
        return [f"{k}={v}" for k, v in merged.items()]

    @override
    def _build_container_config(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        container_cwd: str,
        env_overrides: Mapping[str, str] | None,
        effective_root: Path | None = None,
        category: str = "",
        network_mode: str | None = None,
        owner_id: str | None = None,
        image_override: NotBlankStr | None = None,
    ) -> dict[str, object]:
        """Build the Docker container creation config.

        Args:
            command: Executable name or path.
            args: Command arguments.
            container_cwd: Working directory inside the container.
            env_overrides: Environment variables for the container.
            effective_root: Host path bound at ``/workspace`` (project
                subtree or, when ``None``, the whole workspace root).
            category: Tool category for runtime resolution.
            network_mode: Override the default network mode. Used to
                set ``container:<sidecar_id>`` when sidecar
                enforcement is active.
            owner_id: Lifecycle owner for container labeling.
            image_override: Per-project devcontainer image to run in
                place of the configured sandbox image; ``None`` keeps the
                configured image.  The hardened host config (read-only
                root, ``CapDrop: ALL``, ``no-new-privileges``) still
                applies, so the override image must run under it.

        Returns:
            A dict suitable for ``aiodocker`` container creation.
        """
        env_list = self._merged_env_list(env_overrides)
        host_config = self._build_host_config(effective_root, category=category)
        if network_mode is not None:
            host_config["NetworkMode"] = network_mode
        # ``synthorg.managed=true`` is the canonical label the
        # reconciliation pass filters on at sandbox-subsystem start
        # (see ``synthorg.tools.sandbox.reconciliation``).  Operators
        # MUST NOT strip this label or the orphan cleanup will treat
        # the container as a foreign daemon process and leave it
        # running on restart.
        labels: dict[str, str] = {
            "synthorg.sandbox": "true",
            MANAGED_LABEL: MANAGED_LABEL_VALUE,
            DEPLOYMENT_LABEL: deployment_id_for(self._workspace),
        }
        if owner_id is not None:
            labels["synthorg.sandbox.owner_id"] = owner_id
        return {
            "Image": str(image_override) if image_override else self._config.image,
            "Cmd": [command, *args],
            "WorkingDir": container_cwd,
            "Env": env_list,
            "Labels": labels,
            "HostConfig": host_config,
            "AttachStdout": True,
            "AttachStderr": True,
        }

    @override
    def _with_network_mode(
        self, config: dict[str, object], network_mode: str
    ) -> dict[str, object]:
        """Return *config* with its network namespace joined to *network_mode*.

        Exists so a caller can build the config (which validates the workspace
        and can refuse) before starting the sidecar whose id the mode names.

        Args:
            config: A container config from :meth:`_build_container_config`.
            network_mode: The ``container:<id>`` namespace to join.

        Returns:
            The same config, addressed to that namespace.

        Raises:
            SandboxError: If the config carries no usable ``HostConfig``.
        """
        host_config = config.get("HostConfig")
        if not isinstance(host_config, dict):
            # Refused rather than returned unchanged. The caller only reaches
            # here once the sidecar is already up, and it records the namespace
            # on the handle either way: a silent pass-through would start the
            # container on its own network while the handle claims the isolated
            # one, so the egress enforcement the sidecar exists to provide is
            # gone and nothing above can tell.
            msg = (
                "container config carries no HostConfig, so the sidecar network "
                "namespace cannot be joined and the container would run without "
                "the egress enforcement it was created for"
            )
            logger.warning(DOCKER_EXECUTE_FAILED, error=msg)
            raise SandboxError(msg)
        host_config["NetworkMode"] = network_mode
        return config

    @override
    def _validate_env(
        self,
        env_overrides: Mapping[str, str] | None,
    ) -> list[str]:
        """Validate env_overrides and return the env list.

        Returns:
            List of ``str``.

        Raises:
            SandboxError: If the related operation fails.
        """
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
        effective_root: Path | None = None,
        *,
        category: str = "",
    ) -> dict[str, object]:
        """Build the Docker host config dict binding *effective_root*.

        *effective_root* defaults to the workspace root (whole-workspace
        mount) when not supplied.

        Returns:
            Mapping from ``str`` to ``object``.
        """
        root = effective_root if effective_root is not None else self._workspace
        memory_bytes = self._parse_memory_limit(
            self._config.memory_limit,
        )
        cpu_quota = nano_cpus(self._config.cpu_limit)
        # The mode is stated rather than inherited: Docker copies the
        # mountpoint's mode from the image but not its ownership, so a home
        # the image ships as 0700 for its own user becomes a tmpfs owned by
        # root that the container's user cannot write, and the mount reads as
        # present while every write to it fails.
        tmpfs_spec = f"size={self._config.tmpfs_size},noexec,nosuid,mode={_TMPFS_MODE}"
        tmpfs = {CONTAINER_TMP: tmpfs_spec, SANDBOX_HOME: tmpfs_spec}
        tmpfs.update(dict.fromkeys(self._config.extra_tmpfs_paths, tmpfs_spec))
        host_config: dict[str, object] = {
            **self._workspace_storage(root, category=category),
            "Tmpfs": tmpfs,
            "Memory": memory_bytes,
            "NanoCpus": cpu_quota,
            "NetworkMode": self._config.network,
            "AutoRemove": False,
            "PidsLimit": self._config.pids_limit,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
        }
        # The container keeps the image's own uid: a sandbox running as the
        # backend could act as the process that governs it. Sharing the
        # backend's GROUP instead is what lets a test runner open the sources
        # the agent just wrote, and it is added at run time rather than baked
        # into the image so an operator's devcontainer override takes part
        # without carrying our group.
        #
        # The grant is container-wide while the reason for it is one mount, so
        # it stays proportionate only while the workspace is the sole
        # host-backed path here. That is a property of the host config built
        # above, not of this line, and it is asserted where a second mount
        # would be added rather than guarded by a branch that can never be
        # false.
        share_gid = workspace_share_gid()
        if share_gid is not None:
            host_config["GroupAdd"] = [str(share_gid)]
        else:
            # The one mechanism granting the sandbox reach into the workspace,
            # absent. Every read and write it attempts will fail EACCES with
            # nothing connecting the failure back to this decision, which is
            # the silent no-access state the sharing contract exists to end.
            # Only reachable off POSIX, where the platform has no groups.
            logger.warning(
                SANDBOX_WORKSPACE_SHARING_UNAVAILABLE,
                platform=platform.system(),
                note=(
                    "no POSIX group to share with the sandbox; workspace "
                    "reads and writes from the container will be refused"
                ),
            )
        # Docker rejects ExtraHosts on a container joining another's network
        # namespace, which inherits that namespace owner's /etc/hosts. When a
        # sidecar enforces egress the aliases go on the sidecar instead (see
        # _create_sidecar), so setting them here as well would fail creation.
        if self._config.extra_hosts and not self._needs_sidecar():
            host_config["ExtraHosts"] = list(self._config.extra_hosts)
        runtime = self._resolve_runtime(category)
        if runtime is not None:
            host_config["Runtime"] = runtime
        return host_config

    def _workspace_storage(self, root: Path, *, category: str) -> dict[str, object]:
        """Describe *root* to the daemon in the way the daemon can resolve.

        A host-run process names a path, because its paths are the daemon's. A
        containerised one names the storage its own mount named, because the
        path it holds means nothing on the other side of the socket.

        Args:
            root: The subtree to expose at ``/workspace``, already validated to
                sit within the workspace.
            category: The tool category this container serves, which decides
                whether the workspace is writable.

        Returns:
            The ``Binds`` or ``Mounts`` fragment of the host config.

        Raises:
            SandboxWorkspaceUnmappableError: *root* is not within the workspace
                the mount was resolved for, so the relative address that mount
                needs cannot be computed.
            SandboxSubpathUnsupportedError: The per-execution subtree needs a
                volume subpath this daemon cannot serve. Raised by
                :meth:`WorkspaceMount.child`, because the shipped subpath is
                only known here.
        """
        mount_mode = resolve_mount_mode(category, self._config.mount_mode)
        parent = self._workspace_mount
        if parent is None:
            bind = f"{_to_posix_bind_path(root)}:{CONTAINER_WORKSPACE}:{mount_mode}"
            return {"Binds": [bind]}
        try:
            relative = PurePosixPath(root.relative_to(self._workspace).as_posix())
        except ValueError as exc:
            posix_root = PurePosixPath(root.as_posix())
            msg = (
                f"the execution root {posix_root} is outside the workspace "
                f"{PurePosixPath(self._workspace.as_posix())} this container's "
                "mount was resolved for"
            )
            logger.warning(
                SANDBOX_WORKSPACE_MOUNT_UNRESOLVED,
                reason="root_outside_workspace",
                workspace=str(PurePosixPath(self._workspace.as_posix())),
            )
            raise SandboxWorkspaceUnmappableError(msg) from exc
        mount = parent.child(relative)
        if mount.host_path is not None:
            return {"Binds": [f"{mount.host_path}:{CONTAINER_WORKSPACE}:{mount_mode}"]}
        return {
            "Mounts": [
                {
                    "Type": "volume",
                    "Source": mount.volume,
                    "Target": CONTAINER_WORKSPACE,
                    "ReadOnly": mount_mode is MountMode.READ_ONLY,
                    "VolumeOptions": {"Subpath": mount.subpath},
                }
            ]
        }

    def _resolve_runtime(self, category: str) -> str | None:
        """Resolve the effective container runtime for a category.

        Delegates to the ``SandboxRuntimeResolver`` when available,
        otherwise falls back to ``config.runtime``.

        Returns:
            The matching ``str``, or ``None`` when no match is found.
        """
        if self._runtime_resolver is not None:
            return self._runtime_resolver.resolve_runtime(category)
        return self._config.runtime

    @override
    def _needs_sidecar(self) -> bool:
        """Return ``True`` if sidecar-based network enforcement is needed.

        Enforcement activates when ``allowed_hosts`` is non-empty (or
        ``network_allow_all`` is set) and the default network is not
        ``"none"``.

        Returns:
            ``True`` when the predicate holds, ``False`` otherwise.
        """
        has_rules = bool(
            self._config.allowed_hosts or self._config.network_allow_all,
        )
        return has_rules and self._config.network != "none"

    @override
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
        return parse_memory_limit(limit)

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

        Returns:
            Result of type ``ContainerHandle``.
        """
        strategy = self._lifecycle_strategy
        if strategy_owns or not strategy.reuses_container:
            return await strategy.acquire(
                owner_id=owner_key,
                create_fn=create_fn,
                destroy_fn=self._destroy_handle,
                alive_fn=self._handle_is_alive,
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

    async def execute(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        category: str = "",
        owner_id: str | None = None,
        project_id: NotBlankStr | None = None,
    ) -> SandboxResult:
        """Execute a command inside a Docker container.

        Acquires a container for the resolved lifecycle owner via the
        configured :class:`SandboxLifecycleStrategy`, runs *command* in
        it via ``docker exec``, then either destroys the container
        (per-call / degraded) or leaves it for the strategy to tear
        down (per-agent grace, per-task release, shutdown cleanup).

        Held under an execution lease for its whole span, so a concurrent
        ``cleanup`` waits rather than closing the daemon client mid-command.

        Args:
            command: Executable name or path.
            args: Command arguments.
            cwd: Working directory (defaults to workspace root).
            env_overrides: Extra env vars (only these -- no host leakage).
            timeout: Seconds before the command is killed. Clamped
                to ``config.timeout_seconds`` if larger.
            category: Tool category for per-category runtime selection.
            owner_id: Lifecycle owner (agent ID, task ID, or ``None``).
            project_id: Owning project; ``None`` selects the whole-workspace
                mount.

        Returns:
            A ``SandboxResult`` with captured output and exit status.

        Raises:
            SandboxShuttingDownError: If the backend is tearing down.
            SandboxStartError: If the Docker daemon or image is unavailable.
            SandboxError: If cwd is outside the workspace boundary or
                *env_overrides* set reserved sandbox control variables.
        """
        async with self._execution_lease():
            return await self._execute_leased(
                command=command,
                args=args,
                cwd=cwd,
                env_overrides=env_overrides,
                timeout=timeout,
                category=category,
                owner_id=owner_id,
                project_id=project_id,
            )

    async def _execute_leased(
        self,
        *,
        command: str,
        args: tuple[str, ...],
        cwd: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
        category: str = "",
        owner_id: str | None = None,
        project_id: NotBlankStr | None = None,
    ) -> SandboxResult:
        """Run one command, with the caller already holding the lease.

        Split from :meth:`execute` only so the lease wraps the whole span
        without re-indenting it; every argument means what it does there.

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
            project_id: Owning project; rebinds
                ``<workspace>/projects/<project_id>`` at ``/workspace``
                and prefixes the lifecycle owner key. Falls back to the
                ``project_id`` correlation context; ``None`` selects the
                whole-workspace mount.

        Returns:
            A ``SandboxResult`` with captured output and exit status.

        Raises:
            SandboxStartError: If the Docker daemon or image is unavailable.
            SandboxError: If cwd is outside the workspace boundary or
                *env_overrides* set reserved sandbox control variables.
        """
        pid = str(project_id) if project_id is not None else self._context_project()
        effective_root = await self._project_root(pid)
        # A relative cwd is relative to the mount root, not to whatever
        # directory this service process happens to be running in: resolved
        # against the process, ``src`` lands outside the project and the
        # boundary check below rejects a working directory the caller was
        # entitled to.
        rooted_cwd = None if cwd is None else self._rooted(cwd, effective_root)
        work_dir = rooted_cwd if rooted_cwd is not None else effective_root
        self._validate_cwd(work_dir, effective_root)
        effective_timeout = min(
            timeout if timeout is not None else self._config.timeout_seconds,
            self._config.timeout_seconds,
        )
        container_cwd = self._resolve_cwd_in_container(rooted_cwd, effective_root)
        # Per-task reproducible environment (ambient, set by the worker):
        # the devcontainer image to run in, plus toolchain / PATH
        # additions. Additions are the base; explicit ``env_overrides``
        # win on conflict. They flow through ``_resolve_exec_env`` like
        # any other override, so the credential-sanitise + reserved-var
        # checks still apply.
        active_env = get_active_sandbox_environment()
        image_override = active_env.image_override if active_env is not None else None
        effective_overrides: Mapping[str, str] | None = env_overrides
        if active_env is not None and active_env.env_additions:
            # Declaration-sourced additions are screened through the
            # secret/loader-injection denylist before merging; explicit
            # tool-supplied env_overrides win on conflict and bypass the
            # denylist by design (parity with SubprocessSandbox).
            screened = self._screen_declaration_env(active_env.env_additions)
            effective_overrides = {**screened, **(env_overrides or {})}
        # Validate / resolve the per-command env BEFORE any container
        # work so a reserved-variable rejection never leaks a container.
        exec_env = self._resolve_exec_env(effective_overrides)
        owner_key, strategy_owns = self._resolve_lifecycle(
            owner_id,
            project_id=pid,
            image_override=str(image_override) if image_override else None,
            mount_mode=resolve_mount_mode(category, self._config.mount_mode),
        )
        logger.debug(
            DOCKER_EXECUTE_START,
            command=command,
            args=args,
            cwd=container_cwd,
            timeout=effective_timeout,
            image=str(image_override) if image_override else self._config.image,
            owner_id=owner_key,
        )
        docker = await self._ensure_docker()

        async def _create() -> ContainerHandle:
            """Create.

            Returns:
                Result of type ``ContainerHandle``.
            """
            return await self._create_keepalive_handle(
                docker=docker,
                container_cwd=container_cwd,
                env_overrides=effective_overrides,
                effective_root=effective_root,
                category=category,
                owner_label=owner_key,
                image_override=image_override,
            )

        handle = await self._acquire_owner_handle(
            owner_key=owner_key,
            strategy_owns=strategy_owns,
            create_fn=_create,
        )

        cfg = self._log_shipping_config
        sidecar_logs: tuple[dict[str, JsonValue], ...] = ()
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
