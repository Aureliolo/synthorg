"""MCP catalog install helpers.

Pure functions that materialize ``McpInstallation`` rows into
``MCPServerConfig`` instances and merge them into the base
``MCPConfig`` loaded from YAML. Consumed by the MCP bridge factory
at startup so installed catalog entries become active servers
without touching the user-owned YAML config file.
"""

from synthorg.integrations.connections.models import CatalogEntry  # noqa: TC001
from synthorg.integrations.mcp_catalog.installations import (
    McpInstallation,  # noqa: TC001
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    MCP_SERVER_INSTALL_VALIDATION_FAILED,
)
from synthorg.tools.mcp.config import MCPConfig, MCPServerConfig

logger = get_logger(__name__)

_CONNECTION_ENV_KEY = "SYNTHORG_CONNECTION"


def installation_to_server_config(
    entry: CatalogEntry,
    connection_name: str | None,
) -> MCPServerConfig:
    """Materialize a catalog entry + optional connection into a server config.

    For ``stdio`` transport the returned config runs ``npx -y <npm_package>``
    and stores the bound connection name in ``env[SYNTHORG_CONNECTION]``
    so the bridge's tool runtime can resolve credentials at invocation
    time via the connection catalog.

    Args:
        entry: The catalog entry being installed.
        connection_name: Name of the bound connection, or ``None`` for
            connectionless entries (filesystem, puppeteer, memory).

    Returns:
        A fully-formed ``MCPServerConfig``.

    Raises:
        ValueError: If the catalog entry lacks the fields required for
            its transport (``npm_package`` for stdio).
    """
    env: dict[str, str] = {}
    if connection_name is not None:
        env[_CONNECTION_ENV_KEY] = connection_name

    if entry.transport == "stdio":
        if not entry.npm_package:
            msg = (
                f"Catalog entry '{entry.id}' is stdio but has no "
                "npm_package; cannot materialize server config"
            )
            logger.warning(
                MCP_SERVER_INSTALL_VALIDATION_FAILED,
                entry_id=entry.id,
                reason=msg,
            )
            raise ValueError(msg)
        return MCPServerConfig(
            name=entry.id,
            transport="stdio",
            command="npx",
            args=("-y", entry.npm_package),
            env=env,
        )

    msg = (
        f"Catalog entry '{entry.id}' transport {entry.transport!r} "
        "is not supported by the install materializer"
    )
    logger.warning(
        MCP_SERVER_INSTALL_VALIDATION_FAILED,
        entry_id=entry.id,
        reason=msg,
    )
    raise ValueError(msg)


def merge_installed_servers(
    base_config: MCPConfig,
    installations: tuple[McpInstallation, ...],
    entries_by_id: dict[str, CatalogEntry],
) -> MCPConfig:
    """Overlay catalog installations onto the base MCPConfig.

    Names already present in ``base_config.servers`` win: the YAML
    is treated as authoritative so a user can override a catalog
    install with their own fully-specified server block. Unknown
    catalog entry ids in ``installations`` are skipped with a warning.

    Args:
        base_config: MCPConfig loaded from YAML.
        installations: Rows from the installations repository.
        entries_by_id: Catalog entries keyed by id (typically from
            ``CatalogService.browse()``).

    Returns:
        A new ``MCPConfig`` with installed servers merged in.
    """
    existing_names = {s.name for s in base_config.servers}
    additions: list[MCPServerConfig] = []
    for install in installations:
        entry = entries_by_id.get(install.catalog_entry_id)
        if entry is None:
            logger.warning(
                MCP_SERVER_INSTALL_VALIDATION_FAILED,
                entry_id=install.catalog_entry_id,
                reason="installed entry missing from catalog",
            )
            continue
        if entry.id in existing_names:
            continue
        try:
            server_cfg = installation_to_server_config(
                entry,
                install.connection_name,
            )
        except ValueError:
            continue
        additions.append(server_cfg)

    if not additions:
        return base_config
    return MCPConfig(servers=(*base_config.servers, *additions))
