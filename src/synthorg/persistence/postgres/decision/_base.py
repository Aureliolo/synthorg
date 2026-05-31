# module-kind: code
"""Shared base for the Postgres decision-repository aspect mixins."""

from psycopg_pool import AsyncConnectionPool


class _DecisionRepoBase:
    """Connection-pool seam shared by the aspect mixins.

    Each pool checkout is an independent transaction, so the repository
    coordinates concurrency through the database's
    ``UNIQUE(task_id, version)`` constraint rather than an in-process
    write lock.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    _pool: AsyncConnectionPool

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
