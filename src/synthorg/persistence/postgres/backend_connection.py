"""Connection lifecycle mixin for ``PostgresPersistenceBackend``.

Owns ``connect``, ``disconnect``, ``health_check``, and the
conninfo helpers.  Relies on ``_config``, ``_pool``, ``_lifecycle_lock``,
``_clear_state``, and ``_create_repositories`` declared on the
concrete backend.
"""

import asyncio
import contextlib
import math
from typing import TYPE_CHECKING

import psycopg
from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.backend import (
    PERSISTENCE_BACKEND_ALREADY_CONNECTED,
    PERSISTENCE_BACKEND_CONNECTED,
    PERSISTENCE_BACKEND_CONNECTING,
    PERSISTENCE_BACKEND_CONNECTION_FAILED,
    PERSISTENCE_BACKEND_DISCONNECT_ERROR,
    PERSISTENCE_BACKEND_DISCONNECTED,
    PERSISTENCE_BACKEND_DISCONNECTING,
    PERSISTENCE_BACKEND_HEALTH_CHECK,
)

if TYPE_CHECKING:
    from synthorg.persistence.config import PostgresConfig

logger = get_logger(__name__)


def _build_conninfo(config: PostgresConfig) -> str:
    """Build a libpq conninfo string from a ``PostgresConfig``.

    Uses ``psycopg.conninfo.make_conninfo`` for correct escaping of
    special characters (spaces, backslashes, equals signs) inside
    credentials and identifiers.

    ``connect_timeout`` is rounded up to a whole number of seconds
    because libpq accepts only integer seconds (with a minimum of 2);
    truncating a sub-second value via ``int()`` would round 0.5 down
    to 0 which libpq interprets as "wait indefinitely", silently
    turning a short configured timeout into no timeout at all.

    Returns:
        Result of type ``str``.
    """
    connect_timeout = max(2, math.ceil(config.connect_timeout_seconds))
    return psycopg.conninfo.make_conninfo(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=config.username,
        password=config.password.get_secret_value(),
        sslmode=config.ssl_mode,
        application_name=config.application_name,
        connect_timeout=connect_timeout,
    )


class PostgresConnectionMixin:
    """Connection lifecycle for the Postgres persistence backend."""

    _config: PostgresConfig
    _pool: AsyncConnectionPool | None
    _lifecycle_lock: asyncio.Lock

    def _clear_state(self) -> None:  # pragma: no cover - see concrete
        """Clear state (provided by concrete backend).

        Raises:
            NotImplementedError: Concrete backend has not implemented this method.
        """
        raise NotImplementedError

    def _create_repositories(self) -> None:  # pragma: no cover - see concrete
        """Create repositories (provided by concrete backend).

        Raises:
            NotImplementedError: Concrete backend has not implemented this method.
        """
        raise NotImplementedError

    async def _configure_connection(
        self,
        conn: psycopg.AsyncConnection[object],
    ) -> None:
        """Apply per-connection session parameters.

        Called by the pool for every new connection it creates.  Sets
        ``statement_timeout`` to the configured limit so runaway
        queries are killed server-side.  ``SET`` opens an implicit
        transaction in Postgres, so we commit before returning the
        connection to the pool -- psycopg's configure callback
        contract requires the connection be idle on return.
        """
        if self._config.statement_timeout_ms > 0:
            await conn.execute(
                sql.SQL("SET SESSION statement_timeout = {}").format(
                    sql.Literal(self._config.statement_timeout_ms)
                )
            )
            await conn.commit()

    async def connect(self) -> None:
        """Open the pool and instantiate repositories."""
        async with self._lifecycle_lock:
            if self._pool is not None:
                logger.debug(PERSISTENCE_BACKEND_ALREADY_CONNECTED)
                return

            logger.info(
                PERSISTENCE_BACKEND_CONNECTING,
                host=self._config.host,
                port=self._config.port,
                database=self._config.database,
            )

            pool: AsyncConnectionPool | None = None
            try:
                conninfo = _build_conninfo(self._config)
                pool = AsyncConnectionPool(
                    conninfo,
                    min_size=self._config.pool_min_size,
                    max_size=self._config.pool_max_size,
                    open=False,
                    configure=self._configure_connection,
                )
                await pool.open(
                    wait=True,
                    timeout=self._config.pool_timeout_seconds,
                )
                self._pool = pool
                self._create_repositories()
            except (psycopg.Error, OSError, TimeoutError) as exc:
                await self._cleanup_failed_connect(exc, pool)
            except Exception as exc:
                if isinstance(exc, MemoryError | RecursionError):
                    # Release the pool handle and clear backend state
                    # before the interpreter-critical exception
                    # propagates: a crash + restart must not leak the
                    # partial pool or leave the backend half-connected.
                    # Cleanup failures are suppressed so they cannot
                    # shadow the critical exception.
                    if pool is not None:
                        with contextlib.suppress(Exception):
                            await pool.close()
                    with contextlib.suppress(Exception):
                        self._clear_state()
                    raise
                await self._cleanup_failed_connect(exc, pool)

            logger.info(
                PERSISTENCE_BACKEND_CONNECTED,
                host=self._config.host,
                database=self._config.database,
            )

    async def _cleanup_failed_connect(
        self,
        exc: BaseException,
        pool: AsyncConnectionPool | None,
    ) -> None:
        """Log failure, close partial pool, and raise.

        Raises:
            PersistenceConnectionError: Always.
        """
        logger.warning(
            PERSISTENCE_BACKEND_CONNECTION_FAILED,
            host=self._config.host,
            database=self._config.database,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        if pool is not None:
            try:
                await pool.close()
            except (psycopg.Error, OSError) as cleanup_exc:
                logger.warning(
                    PERSISTENCE_BACKEND_DISCONNECT_ERROR,
                    host=self._config.host,
                    error=safe_error_description(cleanup_exc),
                    error_type=type(cleanup_exc).__name__,
                    context="cleanup_after_connect_failure",
                )
        self._clear_state()
        msg = "Failed to connect to postgres backend"
        raise PersistenceConnectionError(msg) from exc

    async def disconnect(self) -> None:
        """Close the connection pool."""
        async with self._lifecycle_lock:
            if self._pool is None:
                return

            logger.info(
                PERSISTENCE_BACKEND_DISCONNECTING,
                host=self._config.host,
                database=self._config.database,
            )
            try:
                await self._pool.close()
                logger.info(
                    PERSISTENCE_BACKEND_DISCONNECTED,
                    host=self._config.host,
                    database=self._config.database,
                )
            except (psycopg.Error, OSError) as exc:
                logger.warning(
                    PERSISTENCE_BACKEND_DISCONNECT_ERROR,
                    host=self._config.host,
                    error=safe_error_description(exc),
                    error_type=type(exc).__name__,
                )
            finally:
                self._clear_state()

    async def health_check(self) -> bool:
        """Check database connectivity via ``SELECT 1``.

        Bounded by ``pool_timeout_seconds`` so the probe cannot hang
        indefinitely when the pool is exhausted or the server is
        unreachable -- a stuck health check would otherwise block
        orchestration loops that poll backend readiness.  The timeout
        covers the full probe: waiting for a pool connection checkout
        AND executing the query, whichever takes longer.

        Pool state is captured into a local reference while holding
        ``_lifecycle_lock`` so ``disconnect()`` cannot close the pool
        out from under us after the ``None`` check passes.

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.
        """
        async with self._lifecycle_lock:
            pool = self._pool
        if pool is None:
            return False
        try:
            async with asyncio.timeout(self._config.pool_timeout_seconds):
                async with (
                    pool.connection() as conn,
                    conn.cursor() as cur,
                ):
                    await cur.execute("SELECT 1")
                    row = await cur.fetchone()
                    healthy = row is not None
        except (psycopg.Error, OSError, TimeoutError) as exc:
            logger.warning(
                PERSISTENCE_BACKEND_HEALTH_CHECK,
                healthy=False,
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
            )
            return False
        logger.debug(PERSISTENCE_BACKEND_HEALTH_CHECK, healthy=healthy)
        return healthy
