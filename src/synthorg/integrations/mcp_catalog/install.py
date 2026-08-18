"""MCP catalog install helpers.

Pure functions that materialize ``McpInstallation`` rows into
``MCPServerConfig`` instances and merge them into the base
``MCPConfig`` loaded from YAML. Consumed by the MCP bridge factory
at startup so installed catalog entries become active servers
without touching the user-owned YAML config file.
"""

from typing import Final

from synthorg.integrations.connections.models import CatalogEntry
from synthorg.integrations.errors import MCPInstallError, MCPServerUnlaunchableError
from synthorg.integrations.mcp_catalog.installations import (
    McpInstallation,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    MCP_SERVER_INSTALL_VALIDATION_FAILED,
)
from synthorg.observability.events.mcp import MCP_INSTALL_SKIPPED_EXISTING
from synthorg.tools.mcp.config import MCPConfig, MCPServerConfig
from synthorg.tools.mcp.runtime_provision import image_provides, provided_programs

logger = get_logger(__name__)

#: The program an npm-packaged entry is launched through. Declared because the
#: launchability check reads it: a hardcoded string in the config below would
#: be checked against itself.
_NPM_LAUNCHER: Final[str] = "npx"


def _npx_package_arg(entry: CatalogEntry) -> str:
    """Build the version-pinned ``npx`` package spec.

    An unpinned spec resolves ``latest`` on every reconnect, so a pinned
    ``<package>@<version>`` is the supply-chain guard. The caller has already
    rejected an unpinned stdio entry, so a pin is guaranteed here.

    Returns:
        ``<package>@<version>``.
    """
    return f"{entry.npm_package}@{entry.npm_version}"


def _require_launchable(command: str, entry_id: str) -> None:
    """Refuse an entry whose launch program the runtime image lacks.

    Raises:
        MCPServerUnlaunchableError: The image provides no such program.
    """
    if image_provides(command):
        return
    msg = (
        f"Catalog entry '{entry_id}' launches {command!r}, which the MCP "
        f"runtime image does not provide. Servers run in the sandbox image "
        f"(tools.sandbox_image), which provides: {provided_programs()}. Add "
        f"the package to docker/sandbox/apko.yaml and declare the program in "
        f"tools/mcp/runtime_provision.py, or install an entry whose transport "
        f"is streamable_http, which needs no local runtime."
    )
    logger.warning(
        MCP_SERVER_INSTALL_VALIDATION_FAILED,
        entry_id=entry_id,
        command=command,
        reason=msg,
    )
    raise MCPServerUnlaunchableError(msg)


def installation_to_server_config(
    entry: CatalogEntry,
    connection_name: str | None,
) -> MCPServerConfig:
    """Materialize a catalog entry + optional connection into a server config.

    For ``stdio`` transport the returned config runs
    ``npx -y <npm_package>@<npm_version>`` (version-pinned) and records the
    bound ``connection_name`` plus the entry's credential field-to-env-var
    map, so the MCP client resolves the connection's secrets from the catalog
    and injects them into the spawned process environment at connect time
    (never persisting them here, never on the argv).

    Args:
        entry: The catalog entry being installed.
        connection_name: Name of the bound connection, or ``None`` for
            connectionless entries (filesystem, puppeteer, memory).

    Returns:
        A fully-formed ``MCPServerConfig``.

    Raises:
        MCPInstallError: If the catalog entry lacks the fields required for
            its transport (a pinned ``npm_package``/``npm_version`` for stdio).
        MCPServerUnlaunchableError: If no shipped image provides the runtime
            the launch names.
    """
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
            raise MCPInstallError(msg)
        if not entry.npm_version:
            # An unpinned stdio spec would let npx resolve 'latest' on every
            # reconnect, defeating the supply-chain pin; reject rather than
            # launch an unpinned package.
            msg = (
                f"Catalog entry '{entry.id}' is stdio but has no "
                "npm_version; refusing to launch an unpinned package"
            )
            logger.warning(
                MCP_SERVER_INSTALL_VALIDATION_FAILED,
                entry_id=entry.id,
                reason=msg,
            )
            raise MCPInstallError(msg)
        _require_launchable(_NPM_LAUNCHER, entry.id)
        return MCPServerConfig(
            name=entry.id,
            transport="stdio",
            command=_NPM_LAUNCHER,
            args=("-y", _npx_package_arg(entry)),
            connection_name=connection_name,
            credential_env_map=dict(entry.credential_env_map),
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
    raise MCPInstallError(msg)


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
            # The user-authored YAML block wins; note the override so an
            # operator can distinguish it from an entry missing entirely.
            logger.debug(
                MCP_INSTALL_SKIPPED_EXISTING,
                entry_id=entry.id,
                reason="server name already defined in YAML; catalog install skipped",
            )
            continue
        try:
            server_cfg = installation_to_server_config(
                entry,
                install.connection_name,
            )
        except MCPInstallError:
            # Already logged with the entry and the reason. A row that cannot
            # be materialised is skipped rather than fatal: the operator's
            # other installed servers are not this row's hostage.
            continue
        additions.append(server_cfg)

    if not additions:
        return base_config
    return MCPConfig(servers=(*base_config.servers, *additions))
