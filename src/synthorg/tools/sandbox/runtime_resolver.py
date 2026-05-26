"""Sandbox runtime resolver -- gVisor availability and per-category resolution.

Probes the Docker daemon for installed container runtimes and resolves
the effective runtime for each tool category based on per-category
overrides and global defaults, with automatic fallback when the
requested runtime is unavailable.
"""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger
from synthorg.observability.events.sandbox import (
    SANDBOX_GVISOR_AVAILABLE,
    SANDBOX_GVISOR_FALLBACK,
    SANDBOX_GVISOR_PROBE,
    SANDBOX_GVISOR_UNAVAILABLE,
)

if TYPE_CHECKING:
    from synthorg.tools.sandbox.docker_config import DockerSandboxConfig

logger = get_logger(__name__)

_RUNTIME_PROBE_TIMEOUT_SECONDS: Final[float] = 5.0
"""Upper bound on the Docker ``/info`` runtime probe.

``aiodocker`` inherits aiohttp's defaults (``sock_read=300s``); a
wedged daemon would otherwise stall startup for five minutes. Cap
the probe so it degrades to the ``runc`` fallback quickly.
"""


class SandboxRuntimeResolver:
    """Resolve container runtime per category with gVisor fallback.

    Probes the Docker daemon for installed runtimes at construction
    time (via :meth:`probe_available_runtimes`).  At resolution time,
    checks per-category overrides, then the global runtime default,
    falling back to ``None`` (Docker default, typically ``runc``)
    when the requested runtime is not available on the host.

    Args:
        config: Docker sandbox configuration with runtime overrides.
        available_runtimes: Set of runtime names available on the host.
    """

    def __init__(
        self,
        *,
        config: DockerSandboxConfig,
        available_runtimes: frozenset[str],
    ) -> None:
        self._config = config
        self._available = available_runtimes

    @staticmethod
    async def probe_available_runtimes() -> frozenset[str]:
        """Query the Docker daemon for installed container runtimes.

        Uses ``aiodocker`` ``GET /info`` and parses the ``Runtimes``
        field.  Falls back to ``{"runc"}`` when the daemon is
        unreachable or the response lacks runtime information.

        Returns:
            Frozenset of runtime names (e.g. ``frozenset({"runc", "runsc"})``).
        """
        logger.debug(SANDBOX_GVISOR_PROBE)
        try:
            import aiodocker  # noqa: PLC0415

            async with (
                asyncio.timeout(_RUNTIME_PROBE_TIMEOUT_SECONDS),
                aiodocker.Docker() as client,
            ):
                info = await client.system.info()
                runtimes = info.get("Runtimes", {})
                names = frozenset(runtimes.keys()) if runtimes else frozenset({"runc"})
        except TimeoutError:
            logger.warning(
                SANDBOX_GVISOR_UNAVAILABLE,
                reason="docker_info_timeout",
            )
            return frozenset({"runc"})
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                SANDBOX_GVISOR_UNAVAILABLE,
                reason="docker_unavailable",
            )
            return frozenset({"runc"})

        if "runsc" in names:
            logger.info(SANDBOX_GVISOR_AVAILABLE, runtimes=sorted(names))
        else:
            logger.info(
                SANDBOX_GVISOR_UNAVAILABLE,
                reason="runsc_not_installed",
                runtimes=sorted(names),
            )
        return names

    def resolve_runtime(self, category: str) -> str | None:
        """Return the effective runtime for a tool category.

        Resolution order:

        1. ``config.runtime_overrides[category]`` -- if present and
           available on the host.
        2. ``config.runtime`` (global default) -- if set and available.
        3. ``None`` -- use Docker's default runtime.

        When a requested runtime is not available, logs a fallback
        warning and returns ``None``.

        Args:
            category: Tool category name (e.g. ``"code_execution"``).

        Returns:
            Runtime name string, or ``None`` for Docker default.
        """
        # 1. Per-category override.
        override = self._config.runtime_overrides.get(category)
        if override is not None:
            if override in self._available:
                return override
            logger.warning(
                SANDBOX_GVISOR_FALLBACK,
                category=category,
                requested=override,
                available=sorted(self._available),
            )
            # Fall through to the global runtime/default.

        # 2. Global runtime default.
        if self._config.runtime is not None:
            if self._config.runtime in self._available:
                return self._config.runtime
            logger.warning(
                SANDBOX_GVISOR_FALLBACK,
                category=category,
                requested=self._config.runtime,
                available=sorted(self._available),
            )
            return None

        # 3. No override, no global -- Docker default.
        return None
