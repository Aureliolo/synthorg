"""Backend-aware factory for the project-charter repository.

Like the conversational trio, the charter store is deliberately NOT a
``PersistenceBackend`` property: the deep-interview subsystem is opt-in
and wires its store directly off the connected backend handle. Keeping
the concrete-backend imports here means the persistence boundary holds:
no ``api`` / ``meta`` module imports ``aiosqlite`` / ``psycopg``.
"""

from typing import TYPE_CHECKING, cast

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.charter import (
    PERSISTENCE_CHARTER_HANDLE_UNAVAILABLE,
    PERSISTENCE_CHARTER_UNKNOWN_BACKEND,
)

if TYPE_CHECKING:
    import aiosqlite
    from psycopg_pool import AsyncConnectionPool

    # Kept TYPE_CHECKING-only: importing charter_protocol at module level
    # pulls meta.charter (via its enums), whose package init eagerly imports
    # back into charter_protocol, so a fresh import of this factory before
    # meta.charter is loaded raises a partially-initialised ImportError.
    from synthorg.persistence.charter_protocol import CharterRepository
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

_SQLITE: str = "sqlite"
_POSTGRES: str = "postgres"


def build_charter_repository(
    backend: PersistenceBackend | None,
) -> CharterRepository | None:
    """Construct the charter repository for *backend*.

    Returns ``None`` when the backend is absent / not connected, or is
    an unknown variant, so the caller degrades to a 503 rather than
    raising during boot.

    Returns:
        The matching value, or ``None`` when absent.
    """
    if backend is None or not getattr(backend, "is_connected", False):
        return None
    name = backend.backend_name
    if name not in (_SQLITE, _POSTGRES):
        logger.warning(PERSISTENCE_CHARTER_UNKNOWN_BACKEND, backend_name=name)
        return None
    try:
        handle = backend.get_db()
        write_context = backend.write_context
    except PersistenceConnectionError as exc:
        logger.warning(
            PERSISTENCE_CHARTER_HANDLE_UNAVAILABLE,
            backend_name=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if name == _SQLITE:
        from synthorg.persistence.sqlite.charter_repo import (  # noqa: PLC0415
            SQLiteCharterRepository,
        )

        return SQLiteCharterRepository(
            cast("aiosqlite.Connection", handle), write_context=write_context
        )
    from synthorg.persistence.postgres.charter_repo import (  # noqa: PLC0415
        PostgresCharterRepository,
    )

    return PostgresCharterRepository(cast("AsyncConnectionPool", handle))


__all__ = ["build_charter_repository"]
