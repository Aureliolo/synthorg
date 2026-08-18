# module-kind: code
"""The create body an MCP runtime container is asked for.

Split from the transport so the isolation policy reads as one piece: what the
container may reach, what it is labelled with, and which environment it gets
are three answers to the same question, and the transport around them is about
moving bytes. Nothing here performs I/O.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, cast

from aiodocker.types import JSONObject

from synthorg.observability import get_logger
from synthorg.observability.events.mcp import MCP_SANDBOX_RESERVED_ENV_DROPPED
from synthorg.tools.mcp.sandbox import MCPSandboxConfig
from synthorg.tools.sandbox._container_limits import nano_cpus, parse_memory_limit
from synthorg.tools.sandbox.deployment_identity import (
    DEPLOYMENT_LABEL,
    MANAGED_LABEL,
    MANAGED_LABEL_VALUE,
)

logger = get_logger(__name__)

#: Names the server on its container, so ``docker ps`` on a shared daemon says
#: which MCP server a process belongs to.
_MCP_SERVER_LABEL: Final[str] = "synthorg.mcp.server"

#: Writable tmpfs the container's ``$HOME`` and npm cache point at, since the
#: root filesystem is read-only.
CONTAINER_TMP: Final[str] = "/tmp"  # noqa: S108 -- container path, not a host path

#: Enough for one package install; the tmpfs is charged to the container's
#: memory, so it is deliberately smaller than the memory limit.
#:
#: ``noexec`` is deliberately absent, unlike the agent sandbox's otherwise
#: identical spec: the runtime execs the package's own binary out of the npm
#: cache, which lives here because the root is read-only, so ``noexec`` would
#: refuse every launch. It costs little: the package IS the code this
#: container exists to run, so denying it execute permission on one mount
#: does not change what the container is trusted with. ``nosuid`` stays, and
#: with every capability dropped and ``no-new-privileges`` set there is no
#: escalation for a dropped binary to reach for.
_TMPFS_SPEC: Final[str] = "rw,nosuid,size=192m"

#: Environment the runtime needs under a read-only root, plus the
#: supply-chain control that keeps a package's install scripts from running.
_RUNTIME_ENV: Final[Mapping[str, str]] = MappingProxyType(
    {
        "HOME": CONTAINER_TMP,
        "NPM_CONFIG_CACHE": f"{CONTAINER_TMP}/.npm",
        "NPM_CONFIG_IGNORE_SCRIPTS": "true",
    }
)


def container_config(
    command: str,
    args: list[str],
    env: Mapping[str, str],
    sandbox: MCPSandboxConfig,
    server_name: str,
) -> JSONObject:
    """Express the launch and the isolation policy as a create config.

    Returns:
        The container-creation body for the daemon.

    Raises:
        ValueError: The configured memory limit is not a Docker size string.
    """
    return cast(
        "JSONObject",
        {
            "Image": sandbox.image,
            "Cmd": [command, *args],
            "Env": _env_list(env, server_name),
            "Labels": _labels(sandbox, server_name),
            "WorkingDir": CONTAINER_TMP,
            # Attached before the start, so no output frame is missed and the
            # session's first request has somewhere to go.
            "OpenStdin": True,
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "StdinOnce": False,
            "Tty": False,
            "HostConfig": {
                "AutoRemove": False,
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": {CONTAINER_TMP: _TMPFS_SPEC},
                "Memory": parse_memory_limit(sandbox.memory_limit),
                "PidsLimit": sandbox.pids_limit,
                "NanoCpus": nano_cpus(float(sandbox.cpus)),
                "NetworkMode": sandbox.network,
                **({"Runtime": sandbox.runtime} if sandbox.runtime is not None else {}),
            },
        },
    )


def _labels(sandbox: MCPSandboxConfig, server_name: str) -> dict[str, str]:
    """Label the container so the boot reconciliation pass can reclaim it.

    A hard kill of the backend leaves the server running with nothing attached
    to it. ``synthorg.managed`` is what the pass filters on and the deployment
    label is what proves the container is this installation's to remove; a
    container carrying neither is left alone for ever, which is how an
    orphaned runtime would otherwise outlive every reference to it.

    Returns:
        The labels the daemon records on the container.
    """
    labels = {
        MANAGED_LABEL: MANAGED_LABEL_VALUE,
        _MCP_SERVER_LABEL: server_name,
    }
    if sandbox.deployment_id is not None:
        labels[DEPLOYMENT_LABEL] = sandbox.deployment_id
    return labels


def _env_list(env: Mapping[str, str], server_name: str) -> list[str]:
    """Render the container environment, trusted controls last.

    The controls the isolation depends on cannot be supplied by whoever
    configured the server: ``NPM_CONFIG_IGNORE_SCRIPTS=false`` re-enables the
    primary npm RCE vector, and ``HOME`` redirects writes off the one writable
    mount. They win by being merged last, and a collision is reported rather
    than silently overridden, since the operator wrote it expecting it to
    apply.

    Returns:
        The ``KEY=value`` lines the daemon takes.
    """
    for key in env:
        if key in _RUNTIME_ENV:
            logger.warning(
                MCP_SANDBOX_RESERVED_ENV_DROPPED,
                server=server_name,
                key=key,
                effective_value=_RUNTIME_ENV[key],
                note=(
                    "supplied env key collides with a sandbox control; the "
                    "sandbox value below is what the container receives"
                ),
            )
    merged = {**dict(env), **_RUNTIME_ENV}
    return [f"{key}={value}" for key, value in merged.items()]


__all__ = ["CONTAINER_TMP", "container_config"]
