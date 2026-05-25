# module-kind: complex_service
"""Execution + lifecycle-dispatch mixin for ``DockerSandbox``.

Owns the keep-alive container + ``docker exec`` execution model:
owner-key resolution, lifecycle-strategy dispatch, keep-alive container
creation, exec stream draining, owner release, container teardown, and
best-effort log shipping.  Relies on attributes/methods provided by the
concrete :class:`DockerSandbox` and its sibling mixins.

One cohesive responsibility: execute one command in a reusable
keep-alive container under the configured lifecycle strategy. The
phases (resolve reuse owner -> create+start container (with sidecar
when network enforcement is active) -> exec drain with timeout +
container-kill -> teardown -> best-effort log ship) all operate on
the same ``ContainerHandle`` and share the project-prefixed owner-key
shape; splitting fragments the keep-alive contract that the lifecycle
strategies depend on.
"""

import asyncio
import hashlib
import re
import uuid
from typing import TYPE_CHECKING, Any, Final

import structlog.contextvars

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docker import (
    DOCKER_CONTAINER_CREATED,
    DOCKER_EXEC_INSPECT_FAILED,
    DOCKER_EXEC_STREAM_CLOSE_FAILED,
    DOCKER_EXECUTE_FAILED,
    DOCKER_EXECUTE_TIMEOUT,
)
from synthorg.observability.events.sandbox import (
    SANDBOX_CONTAINER_LOGS_COLLECT_FAILED,
    SANDBOX_LIFECYCLE_DISPATCH,
    SANDBOX_LIFECYCLE_OWNER_DEGRADED,
    SANDBOX_LIFECYCLE_RELEASE,
    SANDBOX_SIDECAR_REMOVE_FAILED,
    SANDBOX_SIDECAR_REMOVED,
    SANDBOX_SIDECAR_STARTED,
)
from synthorg.tools.sandbox.container_log_shipper import (
    build_correlation_env,
    collect_sidecar_logs,
    ship_container_logs,
)
from synthorg.tools.sandbox.errors import SandboxStartError
from synthorg.tools.sandbox.lifecycle.config import (
    STRATEGY_PER_AGENT,
    STRATEGY_PER_TASK,
)
from synthorg.tools.sandbox.lifecycle.protocol import ContainerHandle
from synthorg.tools.sandbox.result import SandboxResult

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import aiodocker

    from synthorg.core.clock import Clock
    from synthorg.core.types import NotBlankStr
    from synthorg.observability.config import ContainerLogShippingConfig
    from synthorg.tools.sandbox.credential_manager import (
        SandboxCredentialManager,
    )
    from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
    from synthorg.tools.sandbox.lifecycle.protocol import (
        SandboxLifecycleStrategy,
    )

logger = get_logger(__name__)

_KEEPALIVE_COMMAND: Final[str] = "tail"
_KEEPALIVE_ARGS: Final[tuple[str, ...]] = ("-f", "/dev/null")

# aiodocker exec stream frame identifiers (non-TTY multiplexed stream).
_EXEC_STREAM_STDOUT: Final[int] = 1
_EXEC_STREAM_STDERR: Final[int] = 2

# A reusable lifecycle owner must look like an agent/task identifier:
# a bounded slug (alnum, dash, underscore, colon, dot).  Anything else
# coming through the correlation context or an explicit caller is
# rejected so it cannot become a malformed Docker label or a poisoned
# reuse key; the call degrades to ephemeral per-call instead.
_OWNER_ID_MAX_LEN: Final[int] = 128
_OWNER_ID_RE: Final[re.Pattern[str]] = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")
# Truncated SHA-256 length for the environment-image segment of a reuse
# key: 12 hex chars (48 bits) make accidental cross-image collisions
# negligible while keeping the owner key well under the 128-char cap.
_IMAGE_SEGMENT_HASH_LEN: Final[int] = 12


class DockerSandboxExecMixin:
    """Keep-alive container creation, exec, dispatch, and teardown."""

    # Attributes + collaborator methods supplied by the concrete
    # DockerSandbox and its sibling mixins.  Declared TYPE_CHECKING-only
    # (signatures, no runtime body) so they exist for the type checker
    # but never shadow the real sibling/concrete implementations in the
    # runtime MRO.
    if TYPE_CHECKING:
        _config: DockerSandboxConfig
        _clock: Clock
        _credential_manager: SandboxCredentialManager
        _lifecycle_strategy: SandboxLifecycleStrategy
        _docker: aiodocker.Docker | None
        _log_shipping_config: ContainerLogShippingConfig

        def _validate_env(
            self,
            env_overrides: Mapping[str, str] | None,
        ) -> list[str]: ...

        def _build_container_config(  # noqa: PLR0913
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
        ) -> dict[str, Any]: ...

        def _needs_sidecar(self) -> bool: ...

        async def _create_sidecar(
            self,
            docker: aiodocker.Docker,
        ) -> str: ...

        async def _wait_sidecar_healthy(
            self,
            docker: aiodocker.Docker,
            sidecar_id: str,
        ) -> None: ...

        async def _track_container(
            self,
            container_id: str,
            sidecar_id: str | None,
        ) -> None: ...

        async def _untrack_container(
            self,
            container_id: str,
        ) -> None: ...

        async def _remove_container(
            self,
            docker: aiodocker.Docker,
            container_id: str,
        ) -> bool: ...

        async def _stop_container(
            self,
            docker: aiodocker.Docker,
            container_id: str,
        ) -> None: ...

        @staticmethod
        def _log_execution_outcome(
            command: str,
            args: tuple[str, ...],
            container_id: str,
            returncode: int,
            stderr: str,
        ) -> None: ...

    # ------------------------------------------------------------------
    # Owner-key resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _ephemeral_key() -> str:
        """A unique per-call owner key (no reuse)."""
        return f"per-call:{uuid.uuid4()}"

    @staticmethod
    def _valid_owner(key: str) -> bool:
        """Whether *key* is a safe reuse / Docker-label owner id."""
        return len(key) <= _OWNER_ID_MAX_LEN and _OWNER_ID_RE.match(key) is not None

    def _context_owner(self, strategy_kind: str) -> str | None:
        """Owner id from the structlog correlation context, if any."""
        ctx = structlog.contextvars.get_contextvars()
        if strategy_kind == STRATEGY_PER_AGENT:
            ctx_key = ctx.get("agent_id")
        elif strategy_kind == STRATEGY_PER_TASK:
            ctx_key = ctx.get("task_id")
        else:
            ctx_key = None
        return str(ctx_key) if ctx_key else None

    @staticmethod
    def _context_project() -> str | None:
        """Project id from the structlog correlation context, if any."""
        ctx = structlog.contextvars.get_contextvars()
        value = ctx.get("project_id")
        return str(value) if value else None

    @staticmethod
    def _project_prefixed(
        key: str,
        project_id: str | None,
        image_override: str | None = None,
    ) -> str:
        """Prefix a reusable owner key with project + environment identity.

        Forces a per-agent/per-task reused container to be torn down and
        recreated when the project changes, so a container mounted for
        project A is never reused for project B (the isolation
        guarantee). ``None`` leaves the key unprefixed.

        When *image_override* is set (a per-project reproducible
        environment image is active), a short hash of it is appended so
        a warm container built under one declared image is never reused
        for a run that requires a different image; the new image would
        otherwise be silently ignored. ``None`` (no active environment)
        appends nothing, preserving the prior key shape.
        """
        prefixed = f"{project_id}:{key}" if project_id else key
        if image_override:
            digest = hashlib.sha256(image_override.encode("utf-8")).hexdigest()
            return f"{prefixed}:img-{digest[:_IMAGE_SEGMENT_HASH_LEN]}"
        return prefixed

    def _resolve_lifecycle(
        self,
        owner_id: str | None,
        *,
        project_id: str | None = None,
        image_override: str | None = None,
    ) -> tuple[str, bool]:
        """Resolve the lifecycle owner key and teardown ownership.

        Returns ``(owner_key, strategy_owns_teardown)``.  An explicit
        *owner_id* wins; otherwise, for a reuse strategy the key is
        derived from the structlog correlation context (``agent_id`` for
        per-agent, ``task_id`` for per-task).  A per-call strategy, an
        underivable owner, or a malformed owner all degrade to ephemeral
        per-call (``strategy_owns`` ``False`` so the backend destroys
        the container and the strategy is not poisoned).

        A reusable key is prefixed with ``<project_id>:`` so a container
        mounted for one project is never reused for another, and suffixed
        with the active environment image identity so a container built
        under one declared image is never reused for a different one.

        Args:
            owner_id: Explicit lifecycle owner, or ``None``.
            project_id: Owning project, or ``None`` for the no-project
                execution mode.
            image_override: Active reproducible-environment image, or
                ``None`` when no per-project environment is active.

        Returns:
            ``(owner_key, strategy_owns_teardown)``.
        """
        strategy = self._lifecycle_strategy
        strategy_kind = self._config.lifecycle.strategy

        if owner_id is not None and owner_id.strip():
            key = owner_id.strip()
            if not self._valid_owner(key):
                logger.warning(
                    SANDBOX_LIFECYCLE_OWNER_DEGRADED,
                    strategy=strategy_kind,
                    owner_source="explicit",
                    reason="owner_id failed format validation",
                )
                return self._ephemeral_key(), False
            owns = strategy.reuses_container
            prefixed = self._project_prefixed(key, project_id, image_override)
            if not self._valid_owner(prefixed):
                logger.warning(
                    SANDBOX_LIFECYCLE_OWNER_DEGRADED,
                    strategy=strategy_kind,
                    owner_source="explicit",
                    reason="project-prefixed owner_id failed format validation",
                )
                return self._ephemeral_key(), False
            logger.info(
                SANDBOX_LIFECYCLE_DISPATCH,
                strategy=strategy_kind,
                owner_id=prefixed,
                owner_source="explicit",
                strategy_owns=owns,
            )
            return prefixed, owns

        if not strategy.reuses_container:
            return self._ephemeral_key(), False

        ctx_key = self._context_owner(strategy_kind)
        if ctx_key is not None and self._valid_owner(ctx_key):
            prefixed = self._project_prefixed(ctx_key, project_id, image_override)
            if not self._valid_owner(prefixed):
                logger.warning(
                    SANDBOX_LIFECYCLE_OWNER_DEGRADED,
                    strategy=strategy_kind,
                    owner_source="correlation_context",
                    reason="project-prefixed owner_id failed format validation",
                )
                return self._ephemeral_key(), False
            logger.info(
                SANDBOX_LIFECYCLE_DISPATCH,
                strategy=strategy_kind,
                owner_id=prefixed,
                owner_source="correlation_context",
                strategy_owns=True,
            )
            return prefixed, True

        logger.warning(
            SANDBOX_LIFECYCLE_OWNER_DEGRADED,
            strategy=strategy_kind,
            reason=(
                "no valid explicit owner_id and no usable correlation "
                "context; container will not be reused (ephemeral per-call)"
            ),
        )
        return self._ephemeral_key(), False

    async def release_owner(
        self,
        owner_id: str,
        *,
        project_id: str | None = None,
        image_override: str | None = None,
    ) -> None:
        """Signal that *owner_id* no longer needs its sandbox container.

        Dispatches to the configured lifecycle strategy's ``release``:
        per-agent starts the grace timer, per-task destroys immediately,
        per-call is a no-op.  Wired at the owner boundary (task
        completion / agent stop).

        The key is prefixed with the project (explicit ``project_id`` or
        the correlation context) so it matches the project-prefixed key
        ``execute`` used to acquire the container; otherwise a per-task
        container provisioned under a project would leak until shutdown.

        Args:
            owner_id: The same identifier passed as ``owner_id`` to
                ``execute`` (agent ID for per-agent, task ID for
                per-task).
            project_id: Owning project; falls back to the correlation
                context when ``None``.
            image_override: Active reproducible-environment image used
                when the container was acquired, so the release key
                matches the acquire key. ``None`` when no per-project
                environment was active.
        """
        if not owner_id or not owner_id.strip():
            return
        effective_project = project_id or self._context_project()
        key = self._project_prefixed(
            owner_id.strip(), effective_project, image_override
        )
        if not self._valid_owner(key):
            logger.warning(
                SANDBOX_LIFECYCLE_OWNER_DEGRADED,
                strategy=self._config.lifecycle.strategy,
                reason="project-prefixed owner_id failed format validation",
            )
            return
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

    # ------------------------------------------------------------------
    # Keep-alive container creation
    # ------------------------------------------------------------------

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
            logger.debug(SANDBOX_SIDECAR_STARTED, sidecar_id=sidecar_id[:12])
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

    async def _create_started_container(
        self,
        docker: aiodocker.Docker,
        config: dict[str, Any],
        sidecar_id: str | None,
    ) -> str:
        """Create + track + start the keep-alive container.

        Args:
            docker: Docker client.
            config: Container creation config.
            sidecar_id: Paired sidecar id (cleaned up on failure).

        Returns:
            The started container ID.

        Raises:
            SandboxStartError: If creation or start fails.
        """
        try:
            container = await docker.containers.create(config)  # pyright: ignore[reportAttributeAccessIssue]
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            if sidecar_id:
                await self._cleanup_failed_sidecar(docker, sidecar_id)
            error_desc = safe_error_description(exc)
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                error_type=type(exc).__name__,
                error=error_desc,
            )
            msg = f"Failed to create container: {error_desc}"
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
            sidecar_removed = True
            if sidecar_id:
                sidecar_removed = await self._remove_container(
                    docker,
                    sidecar_id,
                )
            container_removed = await self._remove_container(
                docker,
                container_id,
            )
            # The tracked entry carries sidecar_id, so drop it only when
            # BOTH the container and its paired sidecar are confirmed
            # gone; otherwise a failed sidecar delete with a successful
            # container delete would orphan the sidecar untracked.
            # Keeping the entry lets cleanup()'s sweep retry the
            # survivor.
            if container_removed and sidecar_removed:
                await self._untrack_container(container_id)
            error_desc = safe_error_description(exc)
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=error_desc,
            )
            msg = f"Failed to start container {container_id[:12]}: {error_desc}"
            raise SandboxStartError(msg) from exc
        return container_id

    async def _create_keepalive_handle(  # noqa: PLR0913 -- mount + lifecycle inputs
        self,
        *,
        docker: aiodocker.Docker,
        container_cwd: str,
        env_overrides: Mapping[str, str] | None,
        effective_root: Path,
        category: str,
        owner_label: str,
        image_override: NotBlankStr | None = None,
    ) -> ContainerHandle:
        """Create and start a long-lived idle sandbox container.

        The container runs an idle entrypoint so it survives across many
        ``docker exec`` calls (the reuse model).  When network
        enforcement is active a paired sidecar is created, started, and
        health-checked first; the two share a lifetime.

        Args:
            docker: Docker client.
            container_cwd: Container working directory.
            env_overrides: Environment baked into the container.
            effective_root: Host path bound at ``/workspace`` (project
                subtree or the whole workspace root).
            category: Tool category for runtime resolution.
            owner_label: Lifecycle owner recorded as a container label.
            image_override: Per-project devcontainer image to run in
                place of the configured sandbox image; ``None`` keeps it.

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
            effective_root=effective_root,
            category=category,
            network_mode=network_mode,
            owner_id=owner_label,
            image_override=image_override,
        )
        container_id = await self._create_started_container(
            docker,
            config,
            sidecar_id,
        )
        return ContainerHandle(
            container_id=container_id,
            sidecar_id=sidecar_id,
            network_mode=network_mode or self._config.network,
        )

    # ------------------------------------------------------------------
    # Command execution via docker exec
    # ------------------------------------------------------------------

    async def _open_exec(  # noqa: PLR0913
        self,
        docker: aiodocker.Docker,
        handle: ContainerHandle,
        command: str,
        args: tuple[str, ...],
        container_cwd: str,
        exec_env: dict[str, str],
    ) -> Any:
        """Create an exec instance in the running container.

        Raises:
            SandboxStartError: If the exec instance cannot be created.
        """
        container_id = handle.container_id
        container_obj = docker.containers.container(container_id)  # pyright: ignore[reportAttributeAccessIssue]
        try:
            return await container_obj.exec(
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
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=error_desc,
            )
            msg = (
                f"Failed to exec command in container {container_id[:12]}: {error_desc}"
            )
            raise SandboxStartError(msg) from exc

    async def _drain_exec(
        self,
        docker: aiodocker.Docker,
        exec_obj: Any,
        container_id: str,
        timeout: float,  # noqa: ASYNC109
    ) -> tuple[str, str, bool]:
        """Run the exec stream to completion or timeout.

        Returns:
            ``(stdout, stderr, timed_out)``.
        """
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
            # compromised: the next release/cleanup tears it down, and a
            # re-acquire within the grace window that hits a stopped
            # container surfaces as an error result, never a crash.
            await self._stop_container(docker, container_id)
        finally:
            await self._safe_close_stream(stream)
        return stdout, stderr, timed_out

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
        exec_obj = await self._open_exec(
            docker,
            handle,
            command,
            args,
            container_cwd,
            exec_env,
        )
        start_mono = self._clock.monotonic()
        stdout, stderr, timed_out = await self._drain_exec(
            docker,
            exec_obj,
            container_id,
            timeout,
        )
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
        stderr (2).  Frames are decoded UTF-8 with replacement so binary
        output never raises.

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
                DOCKER_EXEC_STREAM_CLOSE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    @staticmethod
    async def _exec_returncode(exec_obj: Any, container_id: str) -> int:
        """Return the exec exit code, or ``-1`` if it cannot be read."""
        try:
            info = await exec_obj.inspect()
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                DOCKER_EXEC_INSPECT_FAILED,
                container_id=container_id[:12],
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

    # ------------------------------------------------------------------
    # Teardown + log shipping
    # ------------------------------------------------------------------

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
        sidecar_removed = True
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
        # Drop the tracked anchor only once BOTH the sandbox and its
        # paired sidecar are confirmed gone; the entry carries the
        # sidecar id, so untracking on a failed sidecar removal would
        # orphan it (the "remains tracked" warning would also be a
        # lie). Keeping it lets cleanup()'s sweep retry the survivor.
        if sandbox_removed and sidecar_removed:
            await self._untrack_container(handle.container_id)

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
            except Exception as exc:
                logger.warning(
                    SANDBOX_CONTAINER_LOGS_COLLECT_FAILED,
                    sidecar_id=handle.sidecar_id[:12],
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

        _stdout = result.stdout if result is not None else ""
        _stderr = result.stderr if result is not None else ""
        _ms = (result.execution_time_ms or 0) if result is not None else 0
        try:
            await ship_container_logs(
                config=cfg,
                container_id=handle.container_id,
                sidecar_id=handle.sidecar_id,
                stdout=_stdout,
                stderr=_stderr,
                sidecar_logs=sidecar_logs,
                execution_time_ms=_ms,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            # Honour the "never raises ordinary errors" contract: a
            # shipping failure must not propagate into execute()'s
            # finally and skip _teardown_unowned (container leak).
            logger.warning(
                SANDBOX_CONTAINER_LOGS_COLLECT_FAILED,
                container_id=handle.container_id[:12],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        return sidecar_logs
