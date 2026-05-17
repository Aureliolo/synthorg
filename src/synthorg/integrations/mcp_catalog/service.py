"""MCP server catalog service.

Provides browsing, searching, and installation of curated
MCP servers from the bundled catalog.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    CatalogEntry,
    ConnectionType,
)
from synthorg.integrations.errors import (
    CatalogEntryNotFoundError,
    ConnectionNotFoundError,
    InvalidConnectionAuthError,
)
from synthorg.integrations.mcp_catalog.installations import (
    McpInstallation,
    McpInstallationRepository,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    MCP_CATALOG_BROWSED,
    MCP_CATALOG_ENTRY_NOT_FOUND,
    MCP_SERVER_INSTALL_FAILED,
    MCP_SERVER_INSTALL_VALIDATION_FAILED,
)

if TYPE_CHECKING:
    from synthorg.integrations.connections.catalog import ConnectionCatalog

logger = get_logger(__name__)


class InstallationResult(BaseModel):
    """Outcome of a successful MCP catalog install."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    catalog_entry_id: NotBlankStr
    server_name: NotBlankStr
    connection_name: NotBlankStr | None
    tool_count: int


_BUNDLED_PATH = Path(__file__).parent / "bundled.json"


class CatalogService:
    """Browse, search, and install MCP servers from the bundled catalog.

    The catalog is a static JSON file shipped with the package.
    Each entry describes an MCP server with its NPM package, required
    connection type, transport, and capabilities.

    Args:
        catalog_path: Path to the bundled JSON catalog.
    """

    def __init__(
        self,
        catalog_path: Path | None = None,
    ) -> None:
        """Initialize the catalog service.

        Args:
            catalog_path: Override for the bundled catalog JSON file.
                Defaults to the packaged ``bundled.json`` shipped
                alongside this module. Tests pass a temporary path
                to exercise edge cases without shipping fixtures.
        """
        self._path = catalog_path or _BUNDLED_PATH
        self._entries: tuple[CatalogEntry, ...] = ()
        self._loaded = False

    def _load(self) -> None:
        """Load the catalog from disk (lazy, once).

        A corrupt or missing bundled catalog is a release-time
        regression, not a runtime degradation: log the failure
        with full traceback and re-raise so callers see an error
        instead of silently reading an empty catalog.
        """
        if self._loaded:
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            # Guard root/entry shapes so a malformed file like ``[]``
            # or ``{"servers": ["oops"]}`` surfaces through the same
            # logged failure path instead of escaping as an unlogged
            # ``AttributeError`` on the ``raw.get`` / ``s.get`` calls.
            if not isinstance(raw, dict):
                msg = "bundled catalog root must be a JSON object"
                raise TypeError(msg)  # noqa: TRY301
            servers = raw.get("servers", [])
            if not isinstance(servers, list):
                msg = "bundled catalog 'servers' must be a list"
                raise TypeError(msg)  # noqa: TRY301
            entries = []
            for s in servers:
                if not isinstance(s, dict):
                    msg = "bundled catalog entry must be a JSON object"
                    raise TypeError(msg)  # noqa: TRY301
                conn_type = s.get("required_connection_type")
                entries.append(
                    CatalogEntry(
                        id=NotBlankStr(s["id"]),
                        name=NotBlankStr(s["name"]),
                        description=s.get("description", ""),
                        npm_package=(
                            NotBlankStr(s["npm_package"])
                            if s.get("npm_package")
                            else None
                        ),
                        required_connection_type=(
                            ConnectionType(conn_type) if conn_type else None
                        ),
                        transport=s.get("transport", "stdio"),
                        capabilities=tuple(s.get("capabilities", ())),
                        tags=tuple(s.get("tags", ())),
                    ),
                )
        except json.JSONDecodeError, KeyError, FileNotFoundError:
            logger.exception(
                MCP_SERVER_INSTALL_FAILED,
                error="failed to load bundled catalog",
            )
            raise
        except ValueError, TypeError, AttributeError:
            # Catches ``ConnectionType(conn_type)`` enum rejection,
            # the shape-guard TypeErrors above, ``CatalogEntry``
            # Pydantic validation errors, and any residual
            # ``AttributeError`` from an unexpected payload shape
            # (belt-and-braces) so malformed bundled entries always
            # surface as a logged failure instead of escaping silently.
            logger.exception(
                MCP_SERVER_INSTALL_FAILED,
                error="bundled catalog entry failed model validation",
            )
            raise
        self._entries = tuple(entries)
        self._loaded = True

    async def browse(self) -> tuple[CatalogEntry, ...]:
        """Return all catalog entries.

        Returns:
            Tuple of all curated MCP server entries.
        """
        self._load()
        logger.debug(MCP_CATALOG_BROWSED, count=len(self._entries))
        return self._entries

    async def search(self, query: str) -> tuple[CatalogEntry, ...]:
        """Search catalog by name, description, or tags.

        Args:
            query: Search query string (case-insensitive).

        Returns:
            Matching entries.
        """
        self._load()
        q = query.lower()
        return tuple(
            e
            for e in self._entries
            if q in e.name.lower()
            or q in e.description.lower()
            or any(q in tag.lower() for tag in e.tags)
        )

    async def get_entry(self, entry_id: str) -> CatalogEntry:
        """Look up a catalog entry by ID.

        Raises:
            CatalogEntryNotFoundError: If the entry does not exist.
        """
        self._load()
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        logger.warning(
            MCP_CATALOG_ENTRY_NOT_FOUND,
            entry_id=entry_id,
            known_count=len(self._entries),
        )
        msg = f"Catalog entry '{entry_id}' not found"
        raise CatalogEntryNotFoundError(msg)

    async def install(
        self,
        entry_id: str,
        connection_name: str | None,
        *,
        connection_catalog: ConnectionCatalog | None,
        installations_repo: McpInstallationRepository,
    ) -> InstallationResult:
        """Record that a catalog entry has been installed.

        Validates the catalog entry exists and that ``connection_name``
        (when required) resolves to a connection whose type matches
        the entry's ``required_connection_type``. The installation is
        persisted via ``installations_repo`` and picked up by the MCP
        bridge on next reload via
        :func:`synthorg.integrations.mcp_catalog.install.merge_installed_servers`.

        Args:
            entry_id: Catalog entry id to install.
            connection_name: Name of the bound connection, or ``None``
                for connectionless entries.
            connection_catalog: Connection catalog used to validate
                the bound connection. May be ``None`` when the entry
                does not require a connection.
            installations_repo: Where to persist the installation row.

        Returns:
            An :class:`InstallationResult` describing the installed
            server.

        Raises:
            CatalogEntryNotFoundError: If the entry id is unknown.
            ConnectionNotFoundError: If a required connection is
                missing from the catalog.
            InvalidConnectionAuthError: If the bound connection's
                type does not match the entry's requirement.
        """
        entry = await self.get_entry(entry_id)
        resolved_connection_name: str | None = None
        if entry.required_connection_type is not None:
            if not connection_name:
                msg = (
                    f"Catalog entry '{entry_id}' requires a connection "
                    f"of type {entry.required_connection_type.value!r}"
                )
                logger.warning(
                    MCP_SERVER_INSTALL_VALIDATION_FAILED,
                    entry_id=entry_id,
                    reason=msg,
                )
                raise InvalidConnectionAuthError(msg)
            if connection_catalog is None:
                msg = (
                    "Connection catalog is required to install an entry "
                    f"that binds a connection ('{entry_id}')"
                )
                logger.warning(
                    MCP_SERVER_INSTALL_VALIDATION_FAILED,
                    entry_id=entry_id,
                    reason=msg,
                )
                raise InvalidConnectionAuthError(msg)
            conn = await connection_catalog.get(connection_name)
            if conn is None:
                msg = f"Connection '{connection_name}' not found"
                logger.warning(
                    MCP_SERVER_INSTALL_VALIDATION_FAILED,
                    entry_id=entry_id,
                    connection_name=connection_name,
                    reason=msg,
                )
                raise ConnectionNotFoundError(msg)
            if conn.connection_type != entry.required_connection_type:
                msg = (
                    f"Connection '{connection_name}' has type "
                    f"{conn.connection_type.value!r}, but catalog entry "
                    f"'{entry_id}' requires "
                    f"{entry.required_connection_type.value!r}"
                )
                logger.warning(
                    MCP_SERVER_INSTALL_VALIDATION_FAILED,
                    entry_id=entry_id,
                    connection_name=connection_name,
                    reason=msg,
                )
                raise InvalidConnectionAuthError(msg)
            resolved_connection_name = conn.name
        elif connection_name:
            # Entry does not require a connection; ignore and warn.
            logger.warning(
                MCP_SERVER_INSTALL_VALIDATION_FAILED,
                entry_id=entry_id,
                connection_name=connection_name,
                reason=(
                    f"Catalog entry '{entry_id}' does not bind a "
                    "connection; ignoring supplied connection_name"
                ),
            )

        installation = McpInstallation(
            catalog_entry_id=NotBlankStr(entry.id),
            connection_name=(
                NotBlankStr(resolved_connection_name)
                if resolved_connection_name
                else None
            ),
            installed_at=datetime.now(UTC),
        )
        await installations_repo.save(installation)
        return InstallationResult(
            catalog_entry_id=NotBlankStr(entry.id),
            server_name=NotBlankStr(entry.name),
            connection_name=installation.connection_name,
            tool_count=len(entry.capabilities),
        )

    async def uninstall(
        self,
        entry_id: str,
        *,
        installations_repo: McpInstallationRepository,
    ) -> bool:
        """Remove a recorded installation.

        Returns ``True`` when a row was removed. Missing entries
        are a silent no-op so the endpoint can return 200 without
        probing first.
        """
        return await installations_repo.delete(NotBlankStr(entry_id))
