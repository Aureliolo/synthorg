"""Backend-aware factory for the project-charter repository.

Like the conversational trio, the charter store is deliberately NOT a
``PersistenceBackend`` property: the deep-interview subsystem is opt-in
and wires its store directly off the connected backend handle. Keeping
the concrete-backend imports here means the persistence boundary holds:
no ``api`` / ``meta`` module imports ``aiosqlite`` / ``psycopg``.
"""

from typing import TYPE_CHECKING

from synthorg.core.critical_errors import _reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CHARTER_HANDLE_UNAVAILABLE,
    PERSISTENCE_CHARTER_UNKNOWN_BACKEND,
)

if TYPE_CHECKING:
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
    except Exception as exc:
        _reraise_critical(exc)
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

        return SQLiteCharterRepository(handle, write_context=write_context)
    from synthorg.persistence.postgres.charter_repo import (  # noqa: PLC0415
        PostgresCharterRepository,
    )

    return PostgresCharterRepository(handle)


__all__ = ["build_charter_repository"]
