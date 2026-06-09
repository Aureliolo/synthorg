"""Shared connection-pool seam for the Postgres approval repository mixins."""

from psycopg_pool import AsyncConnectionPool


class _ApprovalRepoBase:
    """Holds the shared connection pool for the read / write mixins.

    Args:
        pool: An open psycopg async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool


__all__ = ["_ApprovalRepoBase"]
