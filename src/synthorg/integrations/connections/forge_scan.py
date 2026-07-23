"""Discover the repositories a forge connection's token can reach.

Backs the operator repo-scope selection: the dashboard scans a forge
connection, the operator ticks the in-scope repositories, and the
selection persists as the connection's ``allowed_repos``. Egress is
pinned to the connection's host by construction (the client derives its
API base from the connection ``base_url``), so a scan can never reach
another host.
"""

from typing import Final

from synthorg.engine.workspace.git_backend.forge_api.agent_models import (
    ForgeAccessibleRepo,
)
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.errors import ConnectionNotFoundError

# Bounded page size for the accessible-repo scan (a selection aid, not a
# bulk export): high enough to cover a typical account, capped so a
# malicious/broken forge cannot stream an unbounded page.
_SCAN_LIMIT: Final[int] = 200
_SCAN_TIMEOUT_SECONDS: Final[float] = 20.0


async def scan_accessible_repos(
    catalog: ConnectionCatalog, name: str
) -> tuple[ForgeAccessibleRepo, ...]:
    """List the repositories the bound token can reach for ``name``.

    Args:
        catalog: The connection catalog.
        name: The forge connection name to scan.

    Returns:
        The accessible repositories, each with the token's permission.

    Raises:
        ConnectionNotFoundError: When no connection with that name exists.
        ForgeUnsupportedError: When the connection is not a forge with a
            wired agent-operations client.
        ForgeToolArgumentError: When the forge connection has no base_url.
        ForgeCredentialError: When the connection has no usable token.
    """
    from synthorg.engine.workspace.git_backend.forge_api import (  # noqa: PLC0415
        build_forge_agent_api_client,
        forge_agent_api_supported,
    )
    from synthorg.tools.forge.errors import (  # noqa: PLC0415
        ForgeCredentialError,
        ForgeToolArgumentError,
        ForgeUnsupportedError,
    )

    conn = await catalog.get(name)
    if conn is None:
        msg = f"Connection {name!r} not found"
        raise ConnectionNotFoundError(msg)
    if not forge_agent_api_supported(conn.connection_type):
        msg = f"Connection {name!r} is not a scannable forge"
        raise ForgeUnsupportedError(msg)
    if not conn.base_url:
        msg = f"Forge connection {name!r} has no base_url"
        raise ForgeToolArgumentError(msg)
    credentials = await catalog.get_credentials(name)
    token = credentials.get("token")
    if not token:
        msg = f"Forge connection {name!r} has no token"
        raise ForgeCredentialError(msg)
    client = build_forge_agent_api_client(
        connection_type=conn.connection_type,
        base_url=str(conn.base_url),
        token=token,
        timeout=_SCAN_TIMEOUT_SECONDS,
    )
    try:
        return await client.list_accessible_repos(limit=_SCAN_LIMIT)
    finally:
        await client.aclose()


__all__ = ["scan_accessible_repos"]
