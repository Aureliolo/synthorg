"""Backend-aware factory for the sprint repository.

Like the charter store, the sprint repository is deliberately NOT a
``PersistenceBackend`` property: the agile-sprint subsystem is opt-in
(``agile_kanban`` workflow only) and wires its store directly off the
connected backend handle. Keeping the concrete-backend imports here means
the persistence boundary holds: no ``api`` / ``engine`` module imports
``aiosqlite`` / ``psycopg``.
"""

from typing import TYPE_CHECKING, cast

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.sprint import (
    PERSISTENCE_SPRINT_HANDLE_UNAVAILABLE,
    PERSISTENCE_SPRINT_UNKNOWN_BACKEND,
)
from synthorg.persistence.backend_dispatch import build_for_backend
from synthorg.persistence.sprint_protocol import SprintRepository

if TYPE_CHECKING:
    import aiosqlite
    from psycopg_pool import AsyncConnectionPool

    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)

_SQLITE: str = "sqlite"
_POSTGRES: str = "postgres"


def build_sprint_repository(
    backend: PersistenceBackend | None,
) -> SprintRepository | None:
    """Construct the sprint repository for *backend*.

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
        logger.warning(PERSISTENCE_SPRINT_UNKNOWN_BACKEND, backend_name=name)
        return None
    try:
        handle = backend.get_db()
        write_context = backend.write_context
    except PersistenceConnectionError as exc:
        logger.warning(
            PERSISTENCE_SPRINT_HANDLE_UNAVAILABLE,
            backend_name=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    def _sqlite() -> SprintRepository:
        from synthorg.persistence.sqlite.sprint_repo import (  # noqa: PLC0415
            SQLiteSprintRepository,
        )

        return SQLiteSprintRepository(
            cast("aiosqlite.Connection", handle), write_context=write_context
        )

    def _postgres() -> SprintRepository:
        from synthorg.persistence.postgres.sprint_repo import (  # noqa: PLC0415
            PostgresSprintRepository,
        )

        return PostgresSprintRepository(cast("AsyncConnectionPool", handle))

    return build_for_backend(backend, sqlite=_sqlite, postgres=_postgres)


__all__ = ["build_sprint_repository"]
