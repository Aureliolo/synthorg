"""Schema migration + TimescaleDB setup mixin for ``PostgresPersistenceBackend``.

Owns ``migrate``, ``_apply_timescaledb_setup``, and
``_create_hypertable``.  Relies on ``_config``, ``_pool``, and
``_lifecycle_lock`` / ``_clear_state`` declared on the concrete
backend.
"""

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import TupleRow

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_BACKEND_DISCONNECT_ERROR,
    PERSISTENCE_BACKEND_NOT_CONNECTED,
    PERSISTENCE_TIMESCALEDB_HYPERTABLE_CREATED,
    PERSISTENCE_TIMESCALEDB_SETUP_FAILED,
    PERSISTENCE_TIMESCALEDB_UNAVAILABLE,
)
from synthorg.persistence import migrations

if TYPE_CHECKING:
    import asyncio

    from psycopg_pool import AsyncConnectionPool

    from synthorg.persistence.config import PostgresConfig

logger = get_logger(__name__)


class PostgresMigrationMixin:
    """Schema migration + TimescaleDB setup for the Postgres backend."""

    _config: PostgresConfig
    _pool: AsyncConnectionPool | None
    _lifecycle_lock: asyncio.Lock

    def _clear_state(self) -> None:  # pragma: no cover - see concrete
        """Clear state (provided by concrete backend).

        Raises:
            NotImplementedError: Concrete backend has not implemented this method.
        """
        raise NotImplementedError

    async def migrate(self) -> None:
        """Apply pending schema migrations via yoyo-migrations.

        If migration fails, the pool is closed and backend state is
        cleared so callers cannot continue against a backend whose
        schema is in an indeterminate state (partially applied, or
        rolled back by yoyo).  They must reconnect explicitly.

        When ``config.enable_timescaledb`` is true, the yoyo
        migrations run first, then the hypertable conversion runs as
        a separate post-migration step against the same pool.  The
        hypertable conversion is idempotent (``if_not_exists => TRUE``)
        so repeated runs are safe.

        Raises:
            PersistenceConnectionError: If not connected.
            MigrationError: If migration application fails.
        """
        async with self._lifecycle_lock:
            if self._pool is None:
                msg = "Cannot migrate: postgres backend not connected"
                logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
                raise PersistenceConnectionError(msg)
            db_url = migrations.to_postgres_url(self._config)
            try:
                await migrations.migrate_apply(db_url, backend="postgres")
                if self._config.enable_timescaledb:
                    await self._apply_timescaledb_setup()
            except BaseException:
                pool = self._pool
                if pool is not None:
                    try:
                        await pool.close()
                    except (psycopg.Error, OSError) as cleanup_exc:
                        logger.warning(
                            PERSISTENCE_BACKEND_DISCONNECT_ERROR,
                            host=self._config.host,
                            error_type=type(cleanup_exc).__name__,
                            error=safe_error_description(cleanup_exc),
                            context="cleanup_after_migration_failure",
                        )
                self._clear_state()
                raise

    async def _apply_timescaledb_setup(self) -> None:
        """Convert append-only time-series tables to hypertables.

        Scope: converts ``cost_records`` and ``audit_entries`` to
        hypertables.  ``heartbeats`` is deliberately excluded because
        it is update-heavy (one row per execution_id, bumped per
        pulse) and hypertables optimise for immutable append-only
        data.  Gated on ``config.enable_timescaledb``.  Called at
        the end of ``migrate`` so yoyo has already created the base
        tables with composite primary keys that include the
        partitioning column.  Uses only Apache-2.0 licensed
        TimescaleDB features: ``create_hypertable`` is Apache;
        retention policies and compression are under the Timescale
        License and are NOT used here.  A missing extension is
        treated as a warning (not an error) so operators running
        vanilla Postgres can leave ``enable_timescaledb=True`` in
        their config without breaking the migration.  Rollback of
        any psycopg error is handled by the enclosing ``migrate``
        method (pool close + state clear); this method's try/except
        only exists to tag the log event.

        Raises:
            psycopg.Error: Re-raised after tagging the failure with the
                ``PERSISTENCE_TIMESCALEDB_SETUP_FAILED`` event so the
                enclosing ``migrate`` call can perform pool cleanup.
        """
        assert self._pool is not None  # noqa: S101 -- checked in migrate()
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'",
                )
                if await cur.fetchone() is None:
                    logger.warning(
                        PERSISTENCE_TIMESCALEDB_UNAVAILABLE,
                        host=self._config.host,
                    )
                    await conn.commit()
                    return

                await cur.execute("SET LOCAL statement_timeout = 0")
                await cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
                await self._create_hypertable(
                    cur,
                    "cost_records",
                    self._config.cost_records_chunk_interval,
                )
                await self._create_hypertable(
                    cur,
                    "audit_entries",
                    self._config.audit_entries_chunk_interval,
                )
                await conn.commit()
        except psycopg.Error as exc:
            logger.warning(
                PERSISTENCE_TIMESCALEDB_SETUP_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def _create_hypertable(
        self,
        cur: psycopg.AsyncCursor[TupleRow],
        table: str,
        chunk_interval: str,
    ) -> None:
        """Convert a single append-only table to a TimescaleDB hypertable.

        Idempotent via ``if_not_exists => TRUE`` -- a table that is
        already a hypertable is a no-op.  ``migrate_data => TRUE``
        moves existing rows into chunks on first run; this is a
        full-table rewrite and can be slow on large production
        tables.  Operators should test on a staging clone first.
        """
        await cur.execute(
            "SELECT create_hypertable("
            "%s, 'timestamp', "
            "chunk_time_interval => CAST(%s AS INTERVAL), "
            "if_not_exists => TRUE, "
            "migrate_data => TRUE)",
            (table, chunk_interval),
        )
        logger.info(
            PERSISTENCE_TIMESCALEDB_HYPERTABLE_CREATED,
            table=table,
            chunk_interval=chunk_interval,
        )
