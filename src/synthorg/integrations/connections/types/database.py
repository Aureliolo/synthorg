"""Database connection type."""

from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CONNECTION_VALIDATION_FAILED,
)

logger = get_logger(__name__)

_VALID_DIALECTS = frozenset({"postgres", "mysql", "sqlite", "mariadb"})


class DatabaseAuthenticator:
    """Validates database connection credentials.

    Required fields: ``dialect``, ``host`` (except sqlite),
    ``database``.
    Optional fields: ``port``, ``username``, ``password``.
    """

    @property
    def connection_type(self) -> ConnectionType:
        """The connection type this authenticator handles."""
        return ConnectionType.DATABASE

    def validate_credentials(
        self,
        credentials: dict[str, str],
    ) -> None:
        """Validate credential fields.

        Raises:
            InvalidConnectionAuthError: If ``dialect`` is
                missing/blank/unsupported, ``host`` is missing/blank for
                a non-sqlite dialect, or ``database`` is missing/blank.
        """
        # Type-guard every field before calling ``.strip()`` so a
        # non-string payload surfaces as a structured
        # ``InvalidConnectionAuthError`` instead of ``AttributeError``.
        raw_dialect = credentials.get("dialect")
        dialect = raw_dialect.strip() if isinstance(raw_dialect, str) else ""
        if not dialect:
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.DATABASE.value,
                field="dialect",
                error="missing, non-string, or blank",
            )
            msg = "Database connection requires a 'dialect' field"
            raise InvalidConnectionAuthError(msg)
        if dialect not in _VALID_DIALECTS:
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.DATABASE.value,
                field="dialect",
                error="unsupported dialect",
                dialect=dialect,
                valid=sorted(_VALID_DIALECTS),
            )
            msg = f"Unknown dialect '{dialect}'; supported: {sorted(_VALID_DIALECTS)}"
            raise InvalidConnectionAuthError(msg)
        raw_host = credentials.get("host")
        host = raw_host.strip() if isinstance(raw_host, str) else ""
        if dialect != "sqlite" and not host:
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.DATABASE.value,
                field="host",
                error="missing, non-string, or blank",
                dialect=dialect,
            )
            msg = f"Database dialect '{dialect}' requires a 'host' field"
            raise InvalidConnectionAuthError(msg)
        raw_database = credentials.get("database")
        database = raw_database.strip() if isinstance(raw_database, str) else ""
        if not database:
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_type=ConnectionType.DATABASE.value,
                field="database",
                error="missing, non-string, or blank",
            )
            msg = "Database connection requires a 'database' field"
            raise InvalidConnectionAuthError(msg)

    def required_fields(self) -> tuple[str, ...]:
        """Return required credential field names."""
        return ("dialect", "database")
