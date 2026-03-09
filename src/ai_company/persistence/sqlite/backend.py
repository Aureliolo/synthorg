"""SQLite persistence backend implementation."""

import asyncio
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite

from ai_company.core.types import NotBlankStr
from ai_company.observability import get_logger
from ai_company.observability.events.persistence import (
    PERSISTENCE_BACKEND_ALREADY_CONNECTED,
    PERSISTENCE_BACKEND_CONNECTED,
    PERSISTENCE_BACKEND_CONNECTING,
    PERSISTENCE_BACKEND_CONNECTION_FAILED,
    PERSISTENCE_BACKEND_DISCONNECT_ERROR,
    PERSISTENCE_BACKEND_DISCONNECTED,
    PERSISTENCE_BACKEND_DISCONNECTING,
    PERSISTENCE_BACKEND_HEALTH_CHECK,
    PERSISTENCE_BACKEND_NOT_CONNECTED,
    PERSISTENCE_BACKEND_WAL_MODE_FAILED,
)
from ai_company.persistence.errors import PersistenceConnectionError
from ai_company.persistence.sqlite.migrations import run_migrations
from ai_company.persistence.sqlite.repositories import (
    SQLiteCostRecordRepository,
    SQLiteMessageRepository,
    SQLiteTaskRepository,
)

if TYPE_CHECKING:
    from ai_company.persistence.config import SQLiteConfig

logger = get_logger(__name__)


class SQLitePersistenceBackend:
    """SQLite implementation of the PersistenceBackend protocol.

    Uses a single ``aiosqlite.Connection`` with WAL mode enabled by
    default for file-based databases (in-memory databases do not
    support WAL).  Configurable via ``SQLiteConfig.wal_mode``.

    Args:
        config: SQLite-specific configuration.
    """

    def __init__(self, config: SQLiteConfig) -> None:
        self._config = config
        self._lifecycle_lock = asyncio.Lock()
        self._db: aiosqlite.Connection | None = None
        self._tasks: SQLiteTaskRepository | None = None
        self._cost_records: SQLiteCostRecordRepository | None = None
        self._messages: SQLiteMessageRepository | None = None

    def _clear_state(self) -> None:
        """Reset connection and repository references to ``None``."""
        self._db = None
        self._tasks = None
        self._cost_records = None
        self._messages = None

    async def connect(self) -> None:
        """Open the SQLite database and configure WAL mode."""
        async with self._lifecycle_lock:
            if self._db is not None:
                logger.debug(PERSISTENCE_BACKEND_ALREADY_CONNECTED)
                return

            logger.info(PERSISTENCE_BACKEND_CONNECTING, path=self._config.path)
            try:
                self._db = await aiosqlite.connect(self._config.path)
                self._db.row_factory = aiosqlite.Row

                if self._config.wal_mode:
                    cursor = await self._db.execute("PRAGMA journal_mode=WAL")
                    row = await cursor.fetchone()
                    actual_mode = row[0] if row else "unknown"
                    if actual_mode != "wal" and self._config.path != ":memory:":
                        logger.warning(
                            PERSISTENCE_BACKEND_WAL_MODE_FAILED,
                            requested="wal",
                            actual=actual_mode,
                        )
                    # PRAGMA does not support parameterized queries;
                    # journal_size_limit is validated as int >= 0 by Pydantic.
                    limit = int(self._config.journal_size_limit)
                    await self._db.execute(f"PRAGMA journal_size_limit={limit}")

                self._tasks = SQLiteTaskRepository(self._db)
                self._cost_records = SQLiteCostRecordRepository(self._db)
                self._messages = SQLiteMessageRepository(self._db)
            except (sqlite3.Error, OSError) as exc:
                logger.exception(
                    PERSISTENCE_BACKEND_CONNECTION_FAILED,
                    path=self._config.path,
                    error=str(exc),
                )
                if self._db is not None:
                    try:
                        await self._db.close()
                    except (sqlite3.Error, OSError) as cleanup_exc:
                        logger.warning(
                            PERSISTENCE_BACKEND_DISCONNECT_ERROR,
                            path=self._config.path,
                            error=str(cleanup_exc),
                            error_type=type(cleanup_exc).__name__,
                            context="cleanup_after_connect_failure",
                        )
                self._clear_state()
                msg = "Failed to connect to persistence backend"
                raise PersistenceConnectionError(msg) from exc

            logger.info(PERSISTENCE_BACKEND_CONNECTED, path=self._config.path)

    async def disconnect(self) -> None:
        """Close the database connection."""
        async with self._lifecycle_lock:
            if self._db is None:
                return

            logger.info(PERSISTENCE_BACKEND_DISCONNECTING, path=self._config.path)
            try:
                await self._db.close()
                logger.info(
                    PERSISTENCE_BACKEND_DISCONNECTED,
                    path=self._config.path,
                )
            except (sqlite3.Error, OSError) as exc:
                logger.warning(
                    PERSISTENCE_BACKEND_DISCONNECT_ERROR,
                    path=self._config.path,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            finally:
                self._clear_state()

    async def health_check(self) -> bool:
        """Check database connectivity."""
        if self._db is None:
            return False
        try:
            cursor = await self._db.execute("SELECT 1")
            row = await cursor.fetchone()
            healthy = row is not None
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_BACKEND_HEALTH_CHECK,
                healthy=False,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False
        logger.debug(PERSISTENCE_BACKEND_HEALTH_CHECK, healthy=healthy)
        return healthy

    async def migrate(self) -> None:
        """Run pending schema migrations.

        Raises:
            PersistenceConnectionError: If not connected.
            MigrationError: If migration fails.
        """
        if self._db is None:
            msg = "Cannot migrate: not connected"
            logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
            raise PersistenceConnectionError(msg)
        await run_migrations(self._db)

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an active connection."""
        return self._db is not None

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier."""
        return NotBlankStr("sqlite")

    def _require_connected[T](self, repo: T | None, name: str) -> T:
        """Return *repo* or raise if the backend is not connected.

        Args:
            repo: Repository instance (``None`` when disconnected).
            name: Repository name for the error message.

        Raises:
            PersistenceConnectionError: If *repo* is ``None``.
        """
        if repo is None:
            msg = f"Not connected — call connect() before accessing {name}"
            logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
            raise PersistenceConnectionError(msg)
        return repo

    @property
    def tasks(self) -> SQLiteTaskRepository:
        """Repository for Task persistence.

        Raises:
            PersistenceConnectionError: If not connected.
        """
        return self._require_connected(self._tasks, "tasks")

    @property
    def cost_records(self) -> SQLiteCostRecordRepository:
        """Repository for CostRecord persistence.

        Raises:
            PersistenceConnectionError: If not connected.
        """
        return self._require_connected(self._cost_records, "cost_records")

    @property
    def messages(self) -> SQLiteMessageRepository:
        """Repository for Message persistence.

        Raises:
            PersistenceConnectionError: If not connected.
        """
        return self._require_connected(self._messages, "messages")
