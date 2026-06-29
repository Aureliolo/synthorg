"""Sidecar container mixin for ``DockerSandbox``.

Owns ``_create_sidecar`` and ``_wait_sidecar_healthy``.  Relies on
``_config`` and ``_parse_memory_limit`` declared on the concrete
sandbox.
"""

import asyncio
import secrets
from typing import Final, cast

import aiodocker
from aiodocker.types import JSONObject

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
from synthorg.tools.sandbox._sidecar_resolution import (
    get_resolved_sidecar_limits,
)
from synthorg.tools.sandbox.container_log_shipper import build_correlation_env
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.errors import SandboxStartError

logger = get_logger(__name__)

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

        # One coherent snapshot per launch from the operator-tunable cache so
        # a tools.docker_sidecar_* change applies without a restart and a
        # concurrent hot update cannot mix old/new values within this launch.
        limits = get_resolved_sidecar_limits()
        memory_bytes = self._parse_memory_limit(limits.docker_sidecar_memory_limit)
        nano_cpus = int(limits.docker_sidecar_cpu_limit * _NANO_CPUS_MULTIPLIER)
        tmpfs_spec = f"size={self._config.sidecar_tmpfs_size},noexec,nosuid"

        config: dict[str, object] = {
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
                "PidsLimit": limits.docker_sidecar_max_pids,
                "AutoRemove": False,
                "SecurityOpt": ["no-new-privileges"],
            },
        }

        try:
            container = await docker.containers.create(cast("JSONObject", config))  # pyright: ignore[reportAttributeAccessIssue]
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
        # One coherent snapshot per health-wait so the deadline and the
        # timeout log below report the same operator-tuned value (hot per
        # launch) and cannot mix old/new across the two reads.
        limits = get_resolved_sidecar_limits()
        health_timeout = limits.docker_sidecar_health_timeout_seconds
        poll_interval = limits.docker_sidecar_health_poll_interval_seconds
        loop = asyncio.get_running_loop()
        deadline = loop.time() + health_timeout
        container_obj = docker.containers.container(sidecar_id)  # pyright: ignore[reportAttributeAccessIssue]

        while loop.time() < deadline:
            try:
                info = await container_obj.show()
            except TimeoutError, ConnectionError, OSError:
                await asyncio.sleep(poll_interval)
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

            await asyncio.sleep(poll_interval)

        msg = "Sidecar health check timed out"
        logger.warning(
            SANDBOX_SIDECAR_HEALTH_FAILED,
            sidecar_id=sidecar_id[:12],
            timeout=health_timeout,
        )
        raise SandboxStartError(msg)
