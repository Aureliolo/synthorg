"""Install-time validation for catalog entries that bind a connection.

An entry whose connection cannot supply the credentials it names would
spawn the MCP server unauthenticated, surfacing much later as an opaque
upstream auth failure. Each check below refuses that install up front and
names the field or type at fault, so the catalog service keeps only the
orchestration.
"""

from typing import TYPE_CHECKING

from synthorg.integrations.connections.models import CatalogEntry, ConnectionType
from synthorg.integrations.errors import (
    ConnectionNotFoundError,
    InvalidConnectionAuthError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    MCP_SERVER_INSTALL_VALIDATION_FAILED,
)

if TYPE_CHECKING:
    # ConnectionCatalog is a concrete collaborator injected via a
    # ``FakeConnectionCatalog`` in tests; a runtime import would make
    # typeguard reject the fake.
    from synthorg.integrations.connections.catalog import ConnectionCatalog

logger = get_logger(__name__)


def require_bindable(
    entry: CatalogEntry,
    entry_id: str,
    required_type: ConnectionType,
    connection_name: str | None,
    connection_catalog: ConnectionCatalog | None,
) -> tuple[str, ConnectionCatalog]:
    """Check an entry can bind a connection at all before resolving one.

    Returns:
        The connection name and the catalog to resolve it through, both
        narrowed, so the guarantee survives the hand-off.

    Raises:
        InvalidConnectionAuthError: If the entry declares no credential
            map, names no connection, or has no catalog to resolve it.
    """
    if not entry.credential_env_map:
        # An entry that binds a connection but declares no credential map
        # would spawn unauthenticated while the operator believes the bound
        # connection took effect; reject the misconfigured entry rather than
        # fail silently at connect time.
        msg = (
            f"Catalog entry '{entry_id}' requires a connection but "
            "declares no credential_env_map; it cannot use the "
            "connection's secrets"
        )
    elif not connection_name:
        msg = (
            f"Catalog entry '{entry_id}' requires a connection "
            f"of type {required_type.value!r}"
        )
    elif connection_catalog is None:
        msg = (
            "Connection catalog is required to install an entry "
            f"that binds a connection ('{entry_id}')"
        )
    else:
        return connection_name, connection_catalog
    logger.warning(
        MCP_SERVER_INSTALL_VALIDATION_FAILED,
        entry_id=entry_id,
        reason=msg,
    )
    raise InvalidConnectionAuthError(msg)


async def resolve_matching_connection(
    entry_id: str,
    required_type: ConnectionType,
    connection_name: str,
    connection_catalog: ConnectionCatalog,
) -> str:
    """Resolve the bound connection and check it is the required type.

    Returns:
        The resolved connection's own name.

    Raises:
        ConnectionNotFoundError: If the named connection is missing.
        InvalidConnectionAuthError: If its type is not the one the entry
            requires.
    """
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
    if conn.connection_type != required_type:
        msg = (
            f"Connection '{connection_name}' has type "
            f"{conn.connection_type.value!r}, but catalog entry "
            f"'{entry_id}' requires {required_type.value!r}"
        )
        logger.warning(
            MCP_SERVER_INSTALL_VALIDATION_FAILED,
            entry_id=entry_id,
            connection_name=connection_name,
            reason=msg,
        )
        raise InvalidConnectionAuthError(msg)
    name: str = conn.name
    return name


def require_dialect(
    entry: CatalogEntry,
    entry_id: str,
    connection_name: str,
    credentials: dict[str, str],
) -> None:
    """Check a database connection speaks the dialect the entry needs.

    Raises:
        InvalidConnectionAuthError: If the dialects disagree.
    """
    if entry.required_dialect is None:
        return
    # A database connection's dialect disambiguates entries that share
    # ConnectionType.DATABASE, which the type check cannot. Strip so a
    # stored "  postgres " (which the database authenticator accepts
    # after trimming) is not spuriously rejected here.
    raw_dialect = credentials.get("dialect")
    dialect = raw_dialect.strip() if isinstance(raw_dialect, str) else None
    if dialect == entry.required_dialect:
        return
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


def require_mapped_credentials(
    entry: CatalogEntry,
    entry_id: str,
    connection_name: str,
    credentials: dict[str, str],
) -> None:
    """Refuse an install whose connection lacks a mapped credential field.

    ``credential_env_map`` is resolved by exact field name at connect
    time, with no aliasing: a connection that stores the key under a
    different name injects nothing and the server starts unauthenticated,
    surfacing only as an opaque upstream auth failure much later. The
    entry-side half of this guard is in :func:`require_bindable` (an entry
    that binds a connection must declare a map); this is the
    connection-side half.

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
