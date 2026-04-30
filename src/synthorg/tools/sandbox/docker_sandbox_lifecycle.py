"""Container lifecycle mixin for ``DockerSandbox``.

Owns ``_safe_collect_logs``, ``_log_execution_outcome``,
``_collect_logs``, ``_stop_container``, ``_remove_container``,
``cleanup``, ``health_check``, and ``get_backend_type``.
"""

from typing import TYPE_CHECKING, Final

import aiodocker

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

if TYPE_CHECKING:
    import aiodocker.containers

logger = get_logger(__name__)

_STOP_TIMEOUT_SECONDS: Final[int] = 5
_MAX_STDERR_LOG_CHARS: Final[int] = 200


class DockerSandboxLifecycleMixin:
    """Container log collection, stop/remove, cleanup, health check."""

    _docker: aiodocker.Docker | None
    _tracked_containers: dict[str, str | None]

    async def _ensure_docker(self) -> aiodocker.Docker:  # pragma: no cover
        raise NotImplementedError

    async def _safe_collect_logs(
        self,
        container_obj: aiodocker.containers.DockerContainer,
        container_id: str,
    ) -> tuple[str, str]:
        """Collect logs, returning empty strings on failure."""
        try:
            return await self._collect_logs(container_obj)
        except Exception as exc:
            logger.warning(
                DOCKER_EXECUTE_FAILED,
                container_id=container_id[:12],
                error=f"Log collection failed: {exc}",
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
            await container_obj.stop(
                t=_STOP_TIMEOUT_SECONDS,
            )
            logger.debug(
                DOCKER_CONTAINER_STOPPED,
                container_id=container_id[:12],
            )
        except Exception as exc:
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
        """Remove a container, forcing removal if necessary."""
        try:
            container_obj = docker.containers.container(container_id)  # pyright: ignore[reportAttributeAccessIssue]
            await container_obj.delete(force=True)
            logger.debug(
                DOCKER_CONTAINER_REMOVED,
                container_id=container_id[:12],
            )
        except Exception as exc:
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
        """
        logger.debug(
            DOCKER_CLEANUP,
            tracked_count=len(self._tracked_containers),
        )
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
            except Exception as exc:
                logger.warning(
                    DOCKER_CLEANUP,
                    error=f"Docker client close failed: {exc}",
                )
            finally:
                self._docker = None
        self._tracked_containers = {}

    async def health_check(self) -> bool:
        """Return ``True`` if the Docker daemon is reachable."""
        try:
            docker = await self._ensure_docker()
            await docker.version()
        except Exception as exc:
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
        """Return ``'docker'``."""
        return NotBlankStr("docker")
