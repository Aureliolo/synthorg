# module-kind: adapter
"""Docker sandboxing for stdio MCP servers.

An MCP stdio server is arbitrary third-party code (``npx -y <pkg>``) with full
host access if spawned directly. D16 requires the high-risk execution
categories to run inside Docker; an MCP server executes untrusted code, so it
sits in that set. Rather than build a bespoke container-stdio transport, this
wraps the launch in ``docker run -i`` so the MCP stdio protocol flows over the
container's stdin/stdout while the server runs under cap-drop, no-new-privileges,
a read-only rootfs, and cpu/memory/pid limits.

Credentials are forwarded by NAME (``-e KEY``), never by value on the command
line: the resolved secret travels in the ``docker`` process environment (which
the MCP SDK seeds via ``get_default_environment()`` plus the returned env) and
Docker passes it into the container, so no secret ever appears in host ``argv``.
"""

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.mcp import (
    MCP_SANDBOX_NETWORK_UNSAFE,
    MCP_SANDBOX_RESERVED_ENV_DROPPED,
)

logger = get_logger(__name__)

SandboxNetwork = Literal["bridge", "none", "host"]

# Env keys the sandbox sets as trusted controls. A supplied env key that
# collides with one of these, forwarded after the fixed ``--env=KEY=value``
# flag, would let Docker's last-wins semantics override the control (e.g.
# ``NPM_CONFIG_IGNORE_SCRIPTS=false`` re-enabling install scripts, the primary
# npm RCE vector, or ``HOME`` redirecting writes off the tmpfs). They are never
# forwarded; the trusted values stand.
_RESERVED_ENV_KEYS: Final[frozenset[str]] = frozenset(
    {"HOME", "NPM_CONFIG_CACHE", "NPM_CONFIG_IGNORE_SCRIPTS"}
)


class MCPSandboxConfig(BaseModel):
    """Container-isolation policy for stdio MCP servers.

    Attributes:
        enabled: Whether stdio servers run inside a container. Off is only for
            environments without Docker; it re-exposes host execution.
        image: Container image providing ``npx`` (Node) for the server.
        memory_limit: Docker ``--memory`` value (e.g. ``512m``).
        pids_limit: Maximum processes inside the container.
        cpus: Docker ``--cpus`` quota.
        network: Docker ``--network`` mode. MCP servers reach external APIs, so
            this is ``bridge`` by default rather than ``none``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = True
    image: NotBlankStr = "node:22-alpine"
    memory_limit: NotBlankStr = "512m"
    pids_limit: int = Field(default=256, gt=0)
    cpus: NotBlankStr = "1.0"
    network: SandboxNetwork = "bridge"

    @model_validator(mode="after")
    def _warn_on_host_network(self) -> Self:
        """Surface the ``host`` network mode as an isolation-defeating choice.

        ``host`` shares the host network namespace, so the container can reach
        loopback services and the cloud metadata endpoint -- it defeats the
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


def wrap_stdio_in_sandbox(
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    sandbox: MCPSandboxConfig,
) -> tuple[str, list[str], dict[str, str]]:
    """Rewrite a stdio launch to run inside a hardened container.

    Args:
        command: The original launch command (e.g. ``npx``).
        args: The original command arguments.
        env: Resolved environment (including injected credentials).
        sandbox: The container-isolation policy.

    Returns:
        A ``(command, args, env)`` triple launching the same server via
        ``docker run -i``. The env is returned unchanged: it seeds the docker
        process so ``-e KEY`` forwarding pulls each secret into the container
        by name, keeping secrets out of host ``argv``. ``HOME``/npm cache point
        at the writable tmpfs so ``npx`` works under the read-only rootfs.
    """
    docker_args = [
        "run",
        "--rm",
        "-i",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        # Drop root inside the container: the official node image ships a
        # non-root ``node`` user (uid 1000), which can still write the tmpfs
        # ``HOME``/npm cache below under the read-only rootfs.
        "--user=node",
        "--read-only",
        "--tmpfs=/tmp:rw,nosuid,size=128m",
        f"--memory={sandbox.memory_limit}",
        f"--pids-limit={sandbox.pids_limit}",
        f"--cpus={sandbox.cpus}",
        f"--network={sandbox.network}",
        "--workdir=/tmp",
        "--env=HOME=/tmp",
        "--env=NPM_CONFIG_CACHE=/tmp/.npm",
        # Supply-chain hardening: never run a package's install/postinstall
        # scripts (the primary npm RCE vector), independent of version pinning.
        "--env=NPM_CONFIG_IGNORE_SCRIPTS=true",
    ]
    for key in env:
        if key in _RESERVED_ENV_KEYS:
            # Forwarding this would override a trusted sandbox control.
            logger.warning(
                MCP_SANDBOX_RESERVED_ENV_DROPPED,
                key=key,
                note="supplied env key collides with a sandbox control; dropped",
            )
            continue
        # Forward by name so the value never lands in host argv.
        docker_args.extend(("--env", key))
    docker_args.append(sandbox.image)
    docker_args.append(command)
    docker_args.extend(args)
    return "docker", docker_args, dict(env)
