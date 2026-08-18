# module-kind: adapter
"""Container-isolation policy for stdio MCP servers.

An MCP stdio server is arbitrary third-party code (``npx -y <pkg>``) with full
host access if spawned directly. D16 requires the high-risk execution
categories to run inside Docker; an MCP server executes untrusted code, so it
sits in that set. This module owns the policy an operator configures; the
transport that applies it over the Docker API is
:mod:`synthorg.tools.mcp.container_stdio`.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_SANDBOX_NETWORK_UNSAFE
from synthorg.tools.sandbox._container_limits import nano_cpus, parse_memory_limit
from synthorg.tools.sandbox._image_resolution import get_resolved_sandbox_image

logger = get_logger(__name__)

SandboxNetwork = Literal["bridge", "none", "host"]


class MCPSandboxConfig(BaseModel):
    """Container-isolation policy for stdio MCP servers.

    Attributes:
        enabled: Whether stdio servers run inside a container. Off spawns the
            server as a child of the backend, which the hardened image cannot
            do at all and any other host should not.
        image: Container image providing the runtime the server's command
            needs. There is one image in this product that runs untrusted
            code, so this is the resolved ``tools.sandbox_image``: it carries
            Node, npm and Python, the CLI verifies its signature, and a second
            knob naming a different image would be a second answer to which
            image an operator hardened.
        memory_limit: Memory ceiling as a Docker size string (e.g. ``512m``).
        pids_limit: Maximum processes inside the container.
        cpus: Cpu quota in cores.
        network: Network mode. MCP servers reach external APIs, so this is
            ``bridge`` by default rather than ``none``.
        deployment_id: Which deployment created the container, from
            ``deployment_id_for``. Carried as a label so a container a hard
            kill left behind is reclaimed by the boot reconciliation pass like
            any other managed container. Unset leaves it unattributable, and
            the pass then leaves it alone: "probably ours" and "another
            installation's live work" look identical from the daemon.
        runtime: Container runtime, resolved exactly as the agent sandbox
            resolves it, so an operator who hardened that one with gVisor
            gets the same isolation here. It matters MORE here: with every
            capability dropped and no host path writable, a kernel or runtime
            bug is the only escape left, and this is the one path in the
            product that executes code nobody reviewed. ``None`` means the
            daemon default. There is a single category of work here, so the
            global value is the one to carry.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    image: NotBlankStr = Field(default_factory=get_resolved_sandbox_image)
    memory_limit: NotBlankStr = "512m"
    pids_limit: int = Field(default=256, gt=0)
    cpus: NotBlankStr = "1.0"
    network: SandboxNetwork = "bridge"
    deployment_id: NotBlankStr | None = None
    runtime: NotBlankStr | None = None

    @model_validator(mode="after")
    def _reject_unusable_limits(self) -> Self:
        """Parse both resource limits where they are configured.

        Both travel as free-form strings and are only converted at connect
        time, so a malformed one fails on every reconnect rather than once,
        far from the setting that caused it. The cpu quota is worse than
        malformed-late: the daemon reads a ``NanoCpus`` of zero as "no
        limit", so ``"0"`` does not clamp the container, it uncaps it, and an
        MCP server is the one thing in this product running code nobody
        reviewed.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If either limit cannot be applied as written.
        """
        parse_memory_limit(self.memory_limit)
        nano_cpus(float(self.cpus))
        return self

    @field_validator("cpus")
    @classmethod
    def _cpus_is_a_number(cls, value: NotBlankStr) -> NotBlankStr:
        """Reject a cpu quota that is not a number at all.

        Returns:
            The quota unchanged.

        Raises:
            ValueError: If the quota does not parse as a float.
        """
        try:
            float(value)
        except ValueError as exc:
            msg = f"Cpu quota must be a number of cores, got: {value!r}"
            raise ValueError(msg) from exc
        return value

    @model_validator(mode="after")
    def _warn_on_host_network(self) -> Self:
        """Surface the ``host`` network mode as an isolation-defeating choice.

        ``host`` shares the host network namespace, so the container can reach
        loopback services and the cloud metadata endpoint: it defeats the
        very isolation the sandbox exists to provide. It stays selectable for
        the rare operator who needs it, but never silently.

        Returns:
            Result of type ``Self``.
        """
        if self.network == "host":
            logger.warning(
                MCP_SANDBOX_NETWORK_UNSAFE,
                network=self.network,
                note="host network shares the host namespace; isolation is off",
            )
        return self


__all__ = ["MCPSandboxConfig", "SandboxNetwork"]
