"""MCP server catalog service.

Provides browsing, searching, and installation of curated
MCP servers from the bundled catalog.
"""

import asyncio
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
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.integrations import (
    MCP_CATALOG_BROWSED,
    MCP_CATALOG_ENTRY_NOT_FOUND,
    MCP_SERVER_INSTALL_FAILED,
    MCP_SERVER_INSTALL_VALIDATION_FAILED,
)

if TYPE_CHECKING:
    # ConnectionCatalog is a concrete collaborator injected via a
    # ``FakeConnectionCatalog`` in tests; a runtime import would make
    # typeguard reject the fake.
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
        # Created lazily on first load to avoid binding an asyncio
        # primitive to a specific event loop at construction time.
        self._load_lock: asyncio.Lock | None = None

    async def _ensure_loaded(self) -> None:
        """Load the catalog off-thread once, serialised across callers.

        The blocking JSON read + parse runs via ``asyncio.to_thread`` so
        the event loop is never blocked; an ``asyncio.Lock`` (created on
        first use) ensures only one coroutine performs the load while the
        others await the same result.
        """
        if self._loaded:
            return
        if self._load_lock is None:
            self._load_lock = asyncio.Lock()
        async with self._load_lock:
            # Re-check under the lock: a sibling coroutine may have loaded
            # the catalog while this one awaited the lock.
            if not self._loaded:
                await asyncio.to_thread(self._load)

    def _load(self) -> None:
        """Load the catalog from disk (lazy, once).

        A corrupt or missing bundled catalog is a release-time
        regression, not a runtime degradation: log the failure
        with full traceback and re-raise so callers see an error
        instead of silently reading an empty catalog.

        Raises:
            FileNotFoundError: If the bundled catalog JSON file does not
                exist at ``_path``.
            json.JSONDecodeError: If the catalog file is not valid JSON.
            TypeError: If the catalog root is not a dict, ``servers`` is
                not a list, an entry is not a dict, or a
                ``ConnectionType`` value is invalid.
            KeyError: If a required entry field (``id``, ``name``,
                ``npm_package``) is absent.
            ValueError: If a ``CatalogEntry`` fails Pydantic validation.
            AttributeError: If an unexpected payload shape triggers an
                attribute-access failure during entry parsing.
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
                        npm_version=(
                            NotBlankStr(s["npm_version"])
                            if s.get("npm_version")
                            else None
                        ),
                        required_connection_type=(
                            ConnectionType(conn_type) if conn_type else None
                        ),
                        transport=s.get("transport", "stdio"),
                        capabilities=tuple(s.get("capabilities", ())),
                        tags=tuple(s.get("tags", ())),
                        credential_env_map=dict(s.get("credential_env_map", {})),
                        required_dialect=s.get("required_dialect"),
                    ),
                )
        except (json.JSONDecodeError, KeyError, FileNotFoundError) as exc:
            log_exception_redacted(
                logger,
                MCP_SERVER_INSTALL_FAILED,
                exc,
                reason="failed to load bundled catalog",
            )
            raise
        except (ValueError, TypeError, AttributeError) as exc:
            # Catches ``ConnectionType(conn_type)`` enum rejection,
            # the shape-guard TypeErrors above, ``CatalogEntry``
            # Pydantic validation errors, and any residual
            # ``AttributeError`` from an unexpected payload shape
            # (belt-and-braces) so malformed bundled entries always
            # surface as a logged failure instead of escaping silently.
            log_exception_redacted(
                logger,
                MCP_SERVER_INSTALL_FAILED,
                exc,
                reason="bundled catalog entry failed model validation",
            )
            raise
        self._entries = tuple(entries)
        self._loaded = True

    async def browse(self) -> tuple[CatalogEntry, ...]:
        """Return all catalog entries.

        Returns:
            Tuple of all curated MCP server entries.
        """
        await self._ensure_loaded()
        logger.debug(MCP_CATALOG_BROWSED, count=len(self._entries))
        return self._entries

    async def search(self, query: str) -> tuple[CatalogEntry, ...]:
        """Search catalog by name, description, or tags.

        Args:
            query: Search query string (case-insensitive).

        Returns:
            Matching entries.
        """
        await self._ensure_loaded()
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

        Returns:
            The matching ``CatalogEntry``.

        Raises:
            CatalogEntryNotFoundError: If the entry does not exist.
        """
        await self._ensure_loaded()
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
            if not entry.credential_env_map:
                # An entry that binds a connection but declares no credential
                # map would spawn unauthenticated while the operator believes
                # the bound connection took effect; reject the misconfigured
                # entry rather than fail silently at connect time.
                msg = (
                    f"Catalog entry '{entry_id}' requires a connection but "
                    "declares no credential_env_map; it cannot use the "
                    "connection's secrets"
                )
                logger.warning(
                    MCP_SERVER_INSTALL_VALIDATION_FAILED,
                    entry_id=entry_id,
                    reason=msg,
                )
                raise InvalidConnectionAuthError(msg)
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
            # Resolved once: each call decrypts every secret ref and deep-copies
            # the result, and both checks below read the same credentials.
            creds = await connection_catalog.get_credentials(connection_name)
            if entry.required_dialect is not None:
                # A database connection's dialect disambiguates entries that
                # share ConnectionType.DATABASE, which the type check above
                # cannot. Strip so a stored "  postgres " (which the database
                # authenticator accepts after trimming) is not spuriously
                # rejected here.
                raw_dialect = creds.get("dialect")
                dialect = raw_dialect.strip() if isinstance(raw_dialect, str) else None
                if dialect != entry.required_dialect:
                    msg = (
                        f"Connection '{connection_name}' has dialect "
                        f"{dialect!r}, but catalog entry '{entry_id}' requires "
                        f"{entry.required_dialect!r}"
                    )
                    logger.warning(
                        MCP_SERVER_INSTALL_VALIDATION_FAILED,
                        entry_id=entry_id,
                        connection_name=connection_name,
                        reason=msg,
                    )
                    raise InvalidConnectionAuthError(msg)
            self._require_mapped_credentials(
                entry_id,
                entry,
                connection_name,
                creds,
            )
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

    @staticmethod
    def _require_mapped_credentials(
        entry_id: str,
        entry: CatalogEntry,
        connection_name: str,
        credentials: dict[str, str],
    ) -> None:
        """Refuse an install whose connection lacks a mapped credential field.

        ``credential_env_map`` is resolved by exact field name at connect
        time, with no aliasing: a connection that stores the key under a
        different name injects nothing and the server starts unauthenticated,
        surfacing only as an opaque upstream auth failure much later. The
        entry-side half of this guard is above (an entry that binds a
        connection must declare a map); this is the connection-side half.

        Raises:
            InvalidConnectionAuthError: If any mapped field is absent.
        """
        missing = sorted(
            field for field in entry.credential_env_map if not credentials.get(field)
        )
        if not missing:
            return
        msg = (
            f"Connection '{connection_name}' has no "
            f"{', '.join(repr(field) for field in missing)} credential, which "
            f"catalog entry '{entry_id}' needs; the server would start "
            f"unauthenticated"
        )
        logger.warning(
            MCP_SERVER_INSTALL_VALIDATION_FAILED,
            entry_id=entry_id,
            connection_name=connection_name,
            reason=msg,
        )
        raise InvalidConnectionAuthError(msg)

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

        Returns:
            ``True`` when an installation row was removed; ``False`` when
            no row matched (silent no-op).
        """
        return await installations_repo.delete(NotBlankStr(entry_id))
