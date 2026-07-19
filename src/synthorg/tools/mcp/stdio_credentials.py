"""Stdio credential resolution for MCP servers.

Resolves a bound connection's decrypted credentials into the environment a
stdio MCP server is spawned with, at connect time so secrets are never
persisted in the stored config. Kept separate from the client so the client
module stays focused on connection lifecycle and tool invocation.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.env_var_safety import validate_credential_env_var_name
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.mcp import (
    MCP_CLIENT_CONNECTION_FAILED,
    MCP_CREDENTIAL_SOURCE_MISSING,
    MCP_CREDENTIALS_INJECTED,
)
from synthorg.tools.mcp.config import MCPServerConfig
from synthorg.tools.mcp.errors import MCPConnectionError

logger = get_logger(__name__)


@runtime_checkable
class MCPCredentialResolver(Protocol):
    """Resolves a bound connection's decrypted credentials by name."""

    async def get_credentials(self, name: str) -> dict[str, str]:
        """Return the decrypted credential fields for connection ``name``."""
        ...


async def resolve_stdio_launch(
    config: MCPServerConfig,
    credential_source: MCPCredentialResolver | None,
) -> tuple[list[str], dict[str, str] | None]:
    """Resolve the launch args + env, injecting bound-connection secrets.

    The connection's decrypted credentials are mapped into the environment
    variables (``credential_env_map``) the target server expects, at connect
    time so secrets are never persisted in the stored config. Secrets are
    forwarded by environment variable only: a value never lands in the process
    argv (visible via ``ps`` / ``/proc``). Every enabled-but-unresolvable state
    (bound connection with no map, no resolver, or a short injection) is logged
    loudly rather than silently pretending the server is authenticated.

    Returns:
        The final ``(args, env)`` pair for ``StdioServerParameters``.

    Raises:
        MCPConnectionError: If a credential injection target env-var name is
            unsafe (fails the spawn-boundary re-screen).
    """
    args = list(config.args)
    env = dict(config.env)
    name = config.connection_name
    if name is None:
        return args, (env or None)
    env_map = config.credential_env_map
    if not env_map:
        logger.warning(
            MCP_CREDENTIAL_SOURCE_MISSING,
            server=config.name,
            connection=name,
            note="connection bound but entry declares no credential fields",
        )
        return args, (env or None)
    if credential_source is None:
        logger.warning(
            MCP_CREDENTIAL_SOURCE_MISSING,
            server=config.name,
            connection=name,
            note="connection bound but no credential source; unauthenticated",
        )
        return args, (env or None)
    creds = await credential_source.get_credentials(name)
    injected = 0
    for field, env_var in env_map.items():
        _screen_injection_target(config, env_var)
        value = creds.get(field)
        if value:
            env[env_var] = value
            injected += 1
    _log_injection(config, name, injected, len(env_map))
    return args, (env or None)


def _screen_injection_target(config: MCPServerConfig, env_var: str) -> None:
    """Re-screen a credential target env-var name at the spawn boundary.

    ``credential_env_map`` is screened at config construction, but the model's
    ``frozen=True`` only blocks field reassignment: the nested dict stays
    mutable in place, so a post-validation edit could redirect a secret at a
    loader/process-control variable (``LD_PRELOAD`` / ``NODE_OPTIONS`` /
    ``PATH``). Revalidate here, immediately before the value is injected into
    the child environment, and fail closed rather than spawn with a hijacked
    target.

    Raises:
        MCPConnectionError: If the target env-var name is unsafe.
    """
    try:
        validate_credential_env_var_name(env_var)
    except ValueError as exc:
        logger.warning(
            MCP_CLIENT_CONNECTION_FAILED,
            server=config.name,
            reason="credential injection target rejected at spawn",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"Server {config.name!r}: credential injection target "
            f"env-var name is unsafe"
        )
        raise MCPConnectionError(
            msg,
            context={"server": config.name},
        ) from exc


def _log_injection(
    config: MCPServerConfig,
    connection: str,
    injected: int,
    expected: int,
) -> None:
    """Log credential injection, escalating a short injection to WARNING.

    ``injected < expected`` means the resolved connection is missing a field
    the entry declares (a schema mismatch between the catalog entry and the
    connection's credential type); it presents downstream as an opaque upstream
    auth failure, so it is surfaced here rather than logged identically to a
    full injection.
    """
    if injected < expected:
        logger.warning(
            MCP_CREDENTIALS_INJECTED,
            server=config.name,
            connection=connection,
            injected_fields=injected,
            expected_fields=expected,
            note="fewer credential fields resolved than the entry declares",
        )
        return
    logger.info(
        MCP_CREDENTIALS_INJECTED,
        server=config.name,
        connection=connection,
        injected_fields=injected,
    )
