"""Sidecar container mixin for ``DockerSandbox``.

Owns ``_create_sidecar`` and ``_wait_sidecar_healthy``.  Relies on
``_config`` and ``_parse_memory_limit`` declared on the concrete
sandbox.
"""

import asyncio
import secrets
from typing import TYPE_CHECKING, Any, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.docker import DOCKER_EXECUTE_FAILED
from synthorg.observability.events.sandbox import (
    SANDBOX_NETWORK_ENFORCEMENT,
    SANDBOX_SIDECAR_CREATED,
    SANDBOX_SIDECAR_HEALTH_FAILED,
    SANDBOX_SIDECAR_HEALTHY,
)
from synthorg.tools.sandbox.container_log_shipper import build_correlation_env
from synthorg.tools.sandbox.errors import SandboxStartError

if TYPE_CHECKING:
    import aiodocker

    from synthorg.tools.sandbox.docker_config import DockerSandboxConfig

logger = get_logger(__name__)

_SIDECAR_HEALTH_POLL_INTERVAL: Final[float] = 0.2
_SIDECAR_HEALTH_TIMEOUT: Final[float] = 15.0
_SIDECAR_MEMORY: Final[str] = "64m"
_SIDECAR_CPU: Final[float] = 0.5
_NANO_CPUS_MULTIPLIER: Final[int] = 1_000_000_000


class DockerSandboxSidecarMixin:
    """Sidecar-container creation and health polling."""

    _config: DockerSandboxConfig

    @staticmethod
    def _parse_memory_limit(limit: str) -> int:  # pragma: no cover - see concrete
        """Parse memory limit.

        Returns:
            Result of type ``int``.

        Raises:
            NotImplementedError: If the subclass does not implement this operation.
        """
        raise NotImplementedError

    async def _create_sidecar(
        self,
        docker: aiodocker.Docker,
    ) -> str:
        """Create a sidecar proxy container.

        The sidecar enforces ``allowed_hosts`` via dual-layer DNS +
        DNAT transparent proxy.  It runs on bridge network with
        ``NET_ADMIN`` capability (for iptables DNAT rules).

        Args:
            docker: Docker client.

        Returns:
            The sidecar container ID.

        Raises:
            SandboxStartError: If container creation fails.
        """
        admin_token = secrets.token_urlsafe(32)
        env_list: list[str] = [f"SIDECAR_ADMIN_TOKEN={admin_token}"]

        if self._config.network_allow_all:
            env_list.append("SIDECAR_ALLOW_ALL=1")
        else:
            hosts_csv = ",".join(self._config.allowed_hosts)
            env_list.append(f"SIDECAR_ALLOWED_HOSTS={hosts_csv}")

        dns_flag = "1" if self._config.dns_allowed else "0"
        lo_flag = "1" if self._config.loopback_allowed else "0"
        env_list.append(f"SIDECAR_DNS_ALLOWED={dns_flag}")
        env_list.append(f"SIDECAR_LOOPBACK_ALLOWED={lo_flag}")

        env_list.extend(build_correlation_env())

        memory_bytes = self._parse_memory_limit(_SIDECAR_MEMORY)
        nano_cpus = int(_SIDECAR_CPU * _NANO_CPUS_MULTIPLIER)
        tmpfs_spec = f"size={self._config.sidecar_tmpfs_size},noexec,nosuid"

        config: dict[str, Any] = {
            "Image": self._config.sidecar_image,
            "Env": env_list,
            "HostConfig": {
                "NetworkMode": "bridge",
                "CapDrop": ["ALL"],
                "CapAdd": ["NET_ADMIN"],
                "ReadonlyRootfs": True,
                "Tmpfs": {
                    "/tmp": tmpfs_spec,  # noqa: S108
                    "/run": "size=1m,nosuid",
                },
                "Memory": memory_bytes,
                "NanoCpus": nano_cpus,
                "PidsLimit": self._config.sidecar_pids_limit,
                "AutoRemove": False,
                "SecurityOpt": ["no-new-privileges"],
            },
        }

        try:
            container = await docker.containers.create(config)  # pyright: ignore[reportAttributeAccessIssue]
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to create sidecar container: {safe_error_description(exc)}"
            log_exception_redacted(
                logger, DOCKER_EXECUTE_FAILED, exc, command="sidecar"
            )
            raise SandboxStartError(msg) from exc

        sidecar_id = container.id
        logger.debug(
            SANDBOX_SIDECAR_CREATED,
            sidecar_id=sidecar_id[:12],
            image=self._config.sidecar_image,
        )

        allowed = (
            "allow_all"
            if self._config.network_allow_all
            else ",".join(self._config.allowed_hosts)
        )
        logger.debug(
            SANDBOX_NETWORK_ENFORCEMENT,
            allowed_hosts=allowed,
            dns_allowed=self._config.dns_allowed,
            loopback_allowed=self._config.loopback_allowed,
        )
        return sidecar_id

    async def _wait_sidecar_healthy(
        self,
        docker: aiodocker.Docker,
        sidecar_id: str,
    ) -> None:
        """Wait for the sidecar container to report healthy.

        Polls Docker's built-in health check status every 200ms
        until ``healthy`` or timeout.

        Args:
            docker: Docker client.
            sidecar_id: Sidecar container ID.

        Raises:
            SandboxStartError: On timeout or unhealthy status.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _SIDECAR_HEALTH_TIMEOUT
        container_obj = docker.containers.container(sidecar_id)  # pyright: ignore[reportAttributeAccessIssue]

        while loop.time() < deadline:
            try:
                info = await container_obj.show()
            except TimeoutError, ConnectionError, OSError:
                await asyncio.sleep(_SIDECAR_HEALTH_POLL_INTERVAL)
                continue

            state = info.get("State", {})

            container_status = state.get("Status", "")
            if container_status in ("exited", "dead"):
                msg = (
                    f"Sidecar exited before becoming healthy"
                    f" (status={container_status})"
                )
                logger.warning(
                    SANDBOX_SIDECAR_HEALTH_FAILED,
                    sidecar_id=sidecar_id[:12],
                    status=container_status,
                )
                raise SandboxStartError(msg)

            health_status = state.get("Health", {}).get("Status")
            if health_status == "healthy":
                logger.debug(
                    SANDBOX_SIDECAR_HEALTHY,
                    sidecar_id=sidecar_id[:12],
                )
                return
            if health_status == "unhealthy":
                msg = "Sidecar health check reported unhealthy"
                logger.warning(
                    SANDBOX_SIDECAR_HEALTH_FAILED,
                    sidecar_id=sidecar_id[:12],
                    status=health_status,
                )
                raise SandboxStartError(msg)

            await asyncio.sleep(_SIDECAR_HEALTH_POLL_INTERVAL)

        msg = "Sidecar health check timed out"
        logger.warning(
            SANDBOX_SIDECAR_HEALTH_FAILED,
            sidecar_id=sidecar_id[:12],
            timeout=_SIDECAR_HEALTH_TIMEOUT,
        )
        raise SandboxStartError(msg)
