"""Container lifecycle mixin for ``DockerSandbox``.

Owns ``_safe_collect_logs``, ``_log_execution_outcome``,
``_collect_logs``, ``_stop_container``, ``_remove_container``,
``cleanup``, ``health_check``, and ``get_backend_type``.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import aiodocker
import aiodocker.containers

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docker import (
    DOCKER_CLEANUP,
    DOCKER_CONTAINER_REMOVE_FAILED,
    DOCKER_CONTAINER_REMOVED,
    DOCKER_CONTAINER_STOP_FAILED,
    DOCKER_CONTAINER_STOPPED,
    DOCKER_EXECUTE_FAILED,
    DOCKER_EXECUTE_SUCCESS,
    DOCKER_HEALTH_CHECK,
)
from synthorg.tools.sandbox._sidecar_resolution import (
    get_resolved_docker_stop_grace_timeout_seconds,
)
from synthorg.tools.sandbox.errors import SandboxShuttingDownError
from synthorg.tools.sandbox.lifecycle.protocol import (
    ContainerHandle,
    SandboxLifecycleStrategy,
)
from synthorg.tools.sandbox.workspace_mount import WorkspaceMount

logger = get_logger(__name__)

_MAX_STDERR_LOG_CHARS: Final[int] = 200

#: Slack on top of a command's own timeout before teardown stops waiting for
#: it. A command that outlives its configured deadline is already wedged, and
#: shutdown that waits on it forever is a worse failure than one that reports
#: the abandoned lease and closes.
_DRAIN_GRACE_SECONDS: Final[float] = 10.0


class DockerSandboxLifecycleMixin(ABC):
    """Container log collection, stop/remove, cleanup, health check.

    The docker-handle seams are abstract, bound by the concrete
    ``DockerSandbox``; ABCMeta blocks instantiating a subclass that
    leaves either unimplemented.
    """

    _docker: aiodocker.Docker | None
    _tracked_containers: dict[str, str | None]
    _lifecycle_strategy: SandboxLifecycleStrategy
    #: Serialises client lifecycle: whoever publishes or tears down the client
    #: holds it, so a teardown cannot close a session another call is using.
    _lock: asyncio.Lock
    _workspace_mount: WorkspaceMount | None
    #: Closed to new work by ``cleanup``. Read before a lease is taken, so a
    #: command that arrives during teardown is refused rather than started
    #: against a client about to be closed.
    _shutting_down: bool
    #: How many commands hold a lease right now. ``_idle`` is set exactly when
    #: this is zero, which is the condition teardown waits on.
    _active_executions: int
    _idle: asyncio.Event
    #: How long teardown waits for the leases to drain, derived from the
    #: backend's own command timeout by the concrete sandbox.
    _execution_drain_timeout: float

    @abstractmethod
    async def _ensure_docker(self) -> aiodocker.Docker:
        """Return the connected aiodocker client, connecting if needed."""
        ...

    def _init_execution_leases(self, *, command_timeout: float) -> None:
        """Arm the lease bookkeeping teardown drains on.

        Args:
            command_timeout: The backend's own per-command deadline. Teardown
                waits this plus a fixed grace before abandoning a lease.
        """
        self._shutting_down = False
        self._active_executions = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._execution_drain_timeout = command_timeout + _DRAIN_GRACE_SECONDS

    @asynccontextmanager
    async def _execution_lease(self) -> AsyncIterator[None]:
        """Hold the Docker client open for one command's whole lifecycle.

        ``_lock`` covers publishing and closing the client, not USING it:
        ``execute`` takes the client from ``_ensure_docker`` and then works
        with it unlocked across container creation, exec, log collection and
        teardown. Without a lease over that span a concurrent ``cleanup`` closes
        the session mid-command, which surfaces as a transport error naming the
        session rather than the shutdown that caused it.

        Yields:
            Nothing; the lease is the context itself.

        Raises:
            SandboxShuttingDownError: If teardown has already begun.
        """
        # No await between the check and the increment, so teardown cannot
        # observe an empty lease set while a command is on its way in.
        if self._shutting_down:
            msg = "sandbox is shutting down; no new command can be started"
            raise SandboxShuttingDownError(msg)
        self._active_executions += 1
        self._idle.clear()
        try:
            yield
        finally:
            self._active_executions -= 1
            if self._active_executions == 0:
                self._idle.set()

    async def _drain_executions(self) -> None:
        """Wait for in-flight commands to release their leases."""
        if self._idle.is_set():
            return
        try:
            async with asyncio.timeout(self._execution_drain_timeout):
                await self._idle.wait()
        except TimeoutError:
            # Closing anyway: a command past its own deadline plus grace is
            # not going to finish, and blocking teardown on it forever costs
            # the whole shutdown sequence behind this backend.
            logger.warning(
                DOCKER_CLEANUP,
                reason="execution_drain_timeout",
                active_executions=self._active_executions,
                timeout=self._execution_drain_timeout,
            )

    @abstractmethod
    async def _destroy_handle(self, handle: ContainerHandle) -> None:
        """Destroy the container behind *handle*."""
        ...

    async def _safe_collect_logs(
        self,
        container_obj: aiodocker.containers.DockerContainer,
        container_id: str,
    ) -> tuple[str, str]:
        """Collect logs, returning empty strings on failure.

        Returns:
            Tuple ``(str, str)``.
        """
        try:
            return await self._collect_logs(container_obj)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # aiodocker exceptions can carry the Docker socket path
            # or registry auth header in str(exc).
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                container_id=container_id[:12],
                reason="log_collection_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ("", "")

    @staticmethod
    def _log_execution_outcome(
        command: str,
        args: tuple[str, ...],
        container_id: str,
        returncode: int,
        stderr: str,
    ) -> None:
        """Log the execution outcome at the appropriate level."""
        if returncode != 0:
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                command=command,
                args=args,
                returncode=returncode,
                stderr_length=len(stderr),
                stderr_head=stderr[:_MAX_STDERR_LOG_CHARS],
            )
        else:
            logger.debug(
                DOCKER_EXECUTE_SUCCESS,
                command=command,
                args=args,
                container_id=container_id[:12],
            )

    @staticmethod
    async def _collect_logs(
        container_obj: aiodocker.containers.DockerContainer,
    ) -> tuple[str, str]:
        """Collect stdout and stderr logs from a container.

        Args:
            container_obj: Docker container object.

        Returns:
            Tuple of (stdout, stderr) as strings.
        """
        stdout_logs = await container_obj.log(
            stdout=True,
            stderr=False,
        )
        stderr_logs = await container_obj.log(
            stdout=False,
            stderr=True,
        )
        stdout = "".join(stdout_logs)
        stderr = "".join(stderr_logs)
        return stdout, stderr

    @staticmethod
    async def _stop_container(
        docker: aiodocker.Docker,
        container_id: str,
    ) -> None:
        """Stop a running container."""
        try:
            container_obj = docker.containers.container(container_id)  # pyright: ignore[reportAttributeAccessIssue]
            # Resolved per stop from the operator-tunable cache so a
            # tools.docker_stop_grace_timeout_seconds change is hot.
            await container_obj.stop(
                t=get_resolved_docker_stop_grace_timeout_seconds(),
            )
            logger.debug(
                DOCKER_CONTAINER_STOPPED,
                container_id=container_id[:12],
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DOCKER_CONTAINER_STOP_FAILED,
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    @staticmethod
    async def _remove_container(
        docker: aiodocker.Docker,
        container_id: str,
    ) -> bool:
        """Remove a container, forcing removal if necessary.

        Returns:
            ``True`` if the operation succeeds, ``False`` otherwise.
        """
        try:
            container_obj = docker.containers.container(container_id)  # pyright: ignore[reportAttributeAccessIssue]
            await container_obj.delete(force=True)
            logger.debug(
                DOCKER_CONTAINER_REMOVED,
                container_id=container_id[:12],
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DOCKER_CONTAINER_REMOVE_FAILED,
                container_id=container_id[:12],
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        return True

    async def cleanup(self) -> None:
        """Stop and remove tracked containers, then close the Docker session.

        Removes sandbox containers first, then their paired sidecars,
        to allow graceful network shutdown.

        Closes to new commands and drains the in-flight ones before touching
        the client, so nothing is left holding a session this closes.
        """
        self._shutting_down = True
        logger.debug(
            DOCKER_CLEANUP,
            tracked_count=len(self._tracked_containers),
        )
        await self._drain_executions()
        # Destroy any strategy-owned warm containers and cancel their
        # grace/idle timers first.  ``_destroy_handle`` untracks each
        # container, so the tracked-container sweep below only handles
        # anything the strategy did not own (e.g. an in-flight per-call
        # container during an abrupt shutdown).
        try:
            await self._lifecycle_strategy.cleanup_all(
                destroy_fn=self._destroy_handle,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DOCKER_CLEANUP,
                reason="lifecycle_cleanup_all_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        # Under the same lock `_ensure_docker` publishes the client with:
        # closing it from outside would pull the session out from under a call
        # that already holds it, and the client's lifecycle claims one owner.
        async with self._lock:
            if self._docker is not None:
                for sandbox_id, sidecar_id in list(
                    self._tracked_containers.items(),
                ):
                    await self._stop_container(self._docker, sandbox_id)
                    await self._remove_container(self._docker, sandbox_id)
                    if sidecar_id:
                        await self._stop_container(self._docker, sidecar_id)
                        await self._remove_container(self._docker, sidecar_id)
                try:
                    await self._docker.close()
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        DOCKER_CLEANUP,
                        reason="docker_client_close_failed",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                finally:
                    self._docker = None
                    # Dropped with the client that resolved it, so a later
                    # connect cannot pair a new client with an old mount.
                    self._workspace_mount = None
            self._tracked_containers = {}

    async def health_check(self) -> bool:
        """Return ``True`` if the Docker daemon is reachable.

        Returns:
            ``True`` if the operation succeeds, ``False`` otherwise.
        """
        try:
            docker = await self._ensure_docker()
            await docker.version()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                DOCKER_HEALTH_CHECK,
                healthy=False,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return False
        else:
            logger.debug(
                DOCKER_HEALTH_CHECK,
                healthy=True,
            )
            return True

    def get_backend_type(self) -> NotBlankStr:
        """Return ``'docker'``.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return NotBlankStr("docker")
