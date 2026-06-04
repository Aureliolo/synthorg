"""Shared connection-pool seam for the Postgres subworkflow repo mixins."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


class _SubworkflowRepoBase:
    """Holds the shared connection pool for the CRUD / references mixins.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool


__all__ = ["_SubworkflowRepoBase"]
