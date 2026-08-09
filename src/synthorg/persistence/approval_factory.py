"""Backend-aware factory for the approval repository.

Deliberately NOT exposed as a ``PersistenceBackend`` property (the
precedent the conversational and upgrade-recommendation factories cite):
the store is wired off the connected backend handle. Keeping the
concrete-backend imports here means the persistence boundary holds (no
``api`` module imports ``aiosqlite`` / ``psycopg``).
"""

from typing import TYPE_CHECKING, cast

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.approval_protocol import ApprovalRepository
from synthorg.persistence.backend_dispatch import build_for_backend
from synthorg.persistence.protocol import PersistenceBackend

if TYPE_CHECKING:
    # Driver types used only in ``cast`` string annotations; kept deferred so
    # importing this factory never eagerly pulls the heavy drivers in.
    import aiosqlite
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_SQLITE: str = "sqlite"
_POSTGRES: str = "postgres"


def build_approval_repo(
    backend: PersistenceBackend | None,
) -> ApprovalRepository | None:
    """Construct the approval repository for *backend*.

    Returns:
        The repository, or ``None`` when the backend is absent, not
        connected, or an unknown variant, so the caller degrades to the
        in-memory store rather than raising during boot.
    """
    if backend is None or not getattr(backend, "is_connected", False):
        return None
    name = backend.backend_name
    if name not in (_SQLITE, _POSTGRES):
        logger.warning(
            API_APP_STARTUP,
            service="approval_store",
            note="unknown persistence backend; approvals stay in memory",
            backend_name=name,
        )
        return None
    try:
        handle = backend.get_db()
        write_context = backend.write_context
    except PersistenceConnectionError as exc:
        logger.warning(
            API_APP_STARTUP,
            service="approval_store",
            note="backend handle unavailable; approvals stay in memory",
            backend_name=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    def _sqlite() -> ApprovalRepository:
        from synthorg.persistence.sqlite.approval_repo import (  # noqa: PLC0415
            SQLiteApprovalRepository,
        )

        return SQLiteApprovalRepository(
            cast("aiosqlite.Connection", handle),
            write_context=write_context,
        )

    def _postgres() -> ApprovalRepository:
        from synthorg.persistence.postgres.approval_repo import (  # noqa: PLC0415
            PostgresApprovalRepository,
        )

        return PostgresApprovalRepository(
            cast("AsyncConnectionPool", handle),
        )

    return build_for_backend(backend, sqlite=_sqlite, postgres=_postgres)


__all__ = ["build_approval_repo"]
