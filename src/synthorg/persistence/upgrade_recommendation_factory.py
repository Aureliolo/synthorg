"""Backend-aware factory for the upgrade-recommendation repository.

Deliberately NOT exposed as a ``PersistenceBackend`` property (mirroring
the ``ApprovalRepository`` / conversational-store precedent): the
model-refresh subsystem is opt-in and wires its own store off the
connected backend handle. Keeping the concrete-backend imports here
means the persistence boundary holds (no ``api`` / ``providers`` module
imports ``aiosqlite`` / ``psycopg``).
"""

from typing import TYPE_CHECKING, cast

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.upgrade_recommendation import (
    PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.upgrade_recommendation_protocol import (
    UpgradeRecommendationRepository,
)

if TYPE_CHECKING:
    # Driver types used only in ``cast`` string annotations; kept deferred
    # so importing this factory never eagerly pulls in the heavy aiosqlite
    # / psycopg drivers (the concrete repos import them lazily per-branch).
    import aiosqlite
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_SQLITE: str = "sqlite"
_POSTGRES: str = "postgres"


def build_upgrade_recommendation_repo(
    backend: PersistenceBackend | None,
) -> UpgradeRecommendationRepository | None:
    """Construct the upgrade-recommendation repo for *backend*.

    Returns:
        The repository, or ``None`` when the backend is absent / not
        connected / an unknown variant (so the caller degrades to a 503
        rather than raising during boot).
    """
    if backend is None or not getattr(backend, "is_connected", False):
        return None
    name = backend.backend_name
    if name not in (_SQLITE, _POSTGRES):
        return None
    try:
        handle = backend.get_db()
        write_context = backend.write_context
    except PersistenceConnectionError as exc:
        logger.warning(
            PERSISTENCE_UPGRADE_RECOMMENDATION_FAILED,
            operation="build_repo",
            backend_name=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    if name == _SQLITE:
        from synthorg.persistence.sqlite.upgrade_recommendation_repo import (  # noqa: PLC0415
            SQLiteUpgradeRecommendationRepository,
        )

        return SQLiteUpgradeRecommendationRepository(
            cast("aiosqlite.Connection", handle),
            write_context=write_context,
        )
    from synthorg.persistence.postgres.upgrade_recommendation_repo import (  # noqa: PLC0415
        PostgresUpgradeRecommendationRepository,
    )

    return PostgresUpgradeRecommendationRepository(
        cast("AsyncConnectionPool", handle),
    )


__all__ = ["build_upgrade_recommendation_repo"]
