"""Sidecar container mixin for ``DockerSandbox``.

Owns ``_create_sidecar`` and ``_wait_sidecar_healthy``.  Relies on
``_config`` and ``_parse_memory_limit`` declared on the concrete
sandbox.
"""

import asyncio
import secrets
from abc import ABC, abstractmethod
from typing import cast

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
from synthorg.tools.sandbox._container_limits import nano_cpus
from synthorg.tools.sandbox._mount_paths import CONTAINER_TMP
from synthorg.tools.sandbox._sidecar_resolution import (
    get_resolved_sidecar_limits,
)
from synthorg.tools.sandbox.container_log_shipper import build_correlation_env
from synthorg.tools.sandbox.docker_config import DockerSandboxConfig
from synthorg.tools.sandbox.errors import SandboxStartError

logger = get_logger(__name__)


class DockerSandboxSidecarMixin(ABC):
    """Sidecar-container creation and health polling.

    The memory-limit parsing seam is abstract, bound by the concrete
    ``DockerSandbox``; ABCMeta blocks instantiating a subclass that
    leaves it unimplemented.
    """

    _config: DockerSandboxConfig

    @staticmethod
    @abstractmethod
    def _parse_memory_limit(limit: str) -> int:
        """Parse a docker memory-limit string into a byte count."""
        ...

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
            if self._config.allowed_paths:
                paths_csv = ",".join(self._config.allowed_paths)
                env_list.append(f"SIDECAR_ALLOWED_PATHS={paths_csv}")

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
        cpu_quota = nano_cpus(limits.docker_sidecar_cpu_limit)
        tmpfs_spec = f"size={self._config.sidecar_tmpfs_size},noexec,nosuid"

        # The sandbox container joins this container's network namespace and
        # therefore reads THIS container's /etc/hosts, so an alias the sandbox
        # needs has to be declared here. Docker rejects ExtraHosts on the
        # joining container outright.
        # The sidecar enters as uid 0 and leaves it: Docker cannot deliver a
        # capability to a non-root container process (execve derives the
        # permitted set from file capabilities and the ambient set, both
        # empty, so cap_add leaves only a bounding ceiling), and
        # no-new-privileges rules out file capabilities as the way around
        # that. NET_ADMIN installs the netfilter rules; SETUID/SETGID are
        # what setgroups(2) and setuid(2) themselves require, and the kernel
        # clears every capability as the process descends, so the container
        # spends milliseconds privileged and the rest of its life with none.
        host_config: dict[str, object] = {
            "NetworkMode": "bridge",
            "CapDrop": ["ALL"],
            "CapAdd": ["NET_ADMIN", "SETUID", "SETGID"],
            "ReadonlyRootfs": True,
            "Tmpfs": {
                CONTAINER_TMP: tmpfs_spec,
                "/run": "size=1m,nosuid",
            },
            "Memory": memory_bytes,
            "NanoCpus": cpu_quota,
            "PidsLimit": limits.docker_sidecar_max_pids,
            "AutoRemove": False,
            "SecurityOpt": ["no-new-privileges"],
        }
        if self._config.extra_hosts:
            host_config["ExtraHosts"] = list(self._config.extra_hosts)

        config: dict[str, object] = {
            "Image": self._config.sidecar_image,
            "Env": env_list,
            "HostConfig": host_config,
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
