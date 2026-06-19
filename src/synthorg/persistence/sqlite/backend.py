"""SQLite persistence backend implementation."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import override

import aiosqlite

from synthorg.core.auth.config import AuthConfig
from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.core.types import NotBlankStr
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
    PERSISTENCE_BACKEND_NOT_CONNECTED,
    PERSISTENCE_BACKEND_WAL_MODE_FAILED,
)
from synthorg.ontology.models import EntityDefinition
from synthorg.persistence._shared import format_iso_utc
from synthorg.persistence.auth_protocol import LockoutRepository
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.escalation_protocol import EscalationQueueRepository
from synthorg.persistence.migrations import migrate_apply, to_sqlite_url
from synthorg.persistence.protocol import PersistenceBackendKind
from synthorg.persistence.sqlite._repository_wiring import (
    _SQLiteRepositoryWiring,
)
from synthorg.persistence.sqlite.lockout_repo import (
    SQLiteLockoutRepository,
)
from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)


class SQLitePersistenceBackend(_SQLiteRepositoryWiring):
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
        # Serializes multi-statement transactions on the single
        # aiosqlite connection. Exposed to repos via ``write_context``.
        self._write_lock = asyncio.Lock()
        self._clear_state()

    @property
    def kind(self) -> PersistenceBackendKind:
        """Return the backend discriminator (``SQLITE``).

        Returns:
            Result of type ``PersistenceBackendKind``.
        """
        return PersistenceBackendKind.SQLITE

    @property
    def supports_conversational_approvals(self) -> bool:
        """SQLite cannot durably persist conversational approvals.

        Returns:
            ``False`` until the SQLite ApprovalStore limitation is resolved.
        """
        return False

    @property
    def config(self) -> SQLiteConfig:
        """Public read-only view of the backend's config.

        Exposed so callers that need backend-specific details (the
        backup-handler factory walks the path; tests assert against
        the resolved sqlite path) do not have to reach for the
        private ``_config`` attribute.

        Returns:
            Result of type ``SQLiteConfig``.
        """
        return self._config

    async def connect(self) -> None:
        """Open the SQLite database and configure WAL mode."""
        async with self._lifecycle_lock:
            if self._db is not None:
                logger.debug(PERSISTENCE_BACKEND_ALREADY_CONNECTED)
                return

            logger.info(
                PERSISTENCE_BACKEND_CONNECTING,
                path=self._config.path,
            )
            try:
                self._db = await aiosqlite.connect(self._config.path)
                self._db.row_factory = aiosqlite.Row

                # Enable foreign key enforcement (off by default in SQLite).
                await self._db.execute("PRAGMA foreign_keys = ON")

                if self._config.wal_mode:
                    await self._configure_wal()

                self._create_repositories()
            except (sqlite3.Error, OSError) as exc:
                await self._cleanup_failed_connect(exc)

            logger.info(
                PERSISTENCE_BACKEND_CONNECTED,
                path=self._config.path,
            )

    async def _configure_wal(self) -> None:
        """Configure WAL journal mode and size limit.

        Must only be called when ``self._db`` is not ``None``.
        """
        assert self._db is not None  # noqa: S101
        async with self._db.execute("PRAGMA journal_mode=WAL") as cursor:
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

    def get_db(self) -> aiosqlite.Connection:
        """Return the shared database connection.

        Raises:
            PersistenceConnectionError: If not yet connected.

        Returns:
            The active database connection (raises if not connected).
        """
        if self._db is None:
            msg = "Database not connected"
            raise PersistenceConnectionError(msg)
        return self._db

    @override
    @asynccontextmanager
    async def write_context(self) -> AsyncIterator[None]:
        """Acquire the shared write lock for the lifetime of the block.

        Multi-statement transactions on the single ``aiosqlite.Connection``
        must serialize so a sibling repo's INSERT cannot interleave
        between this repo's INSERT and COMMIT. See
        ``PersistenceBackend.write_context`` for the cross-backend
        contract.
        """
        async with self._write_lock:
            yield

    async def _cleanup_failed_connect(self, exc: sqlite3.Error | OSError) -> None:
        """Log failure, close partial connection, and raise.

        Raises:
            PersistenceConnectionError: Always.
        """
        logger.warning(
            PERSISTENCE_BACKEND_CONNECTION_FAILED,
            path=self._config.path,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        if self._db is not None:
            try:
                await self._db.close()
            except (sqlite3.Error, OSError) as cleanup_exc:
                logger.warning(
                    PERSISTENCE_BACKEND_DISCONNECT_ERROR,
                    path=self._config.path,
                    error=safe_error_description(cleanup_exc),
                    error_type=type(cleanup_exc).__name__,
                    context="cleanup_after_connect_failure",
                )
        self._clear_state()
        msg = "Failed to connect to persistence backend"
        raise PersistenceConnectionError(msg) from exc

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
                    error=safe_error_description(exc),
                    error_type=type(exc).__name__,
                )
            finally:
                self._clear_state()

    async def health_check(self) -> bool:
        """Check database connectivity.

        The ``SELECT 1`` probe is bounded by
        ``config.health_timeout_seconds``: a wedged connection (a held
        write lock, a stalled disk) is reported unhealthy rather than
        hanging the readiness probe indefinitely. This mirrors the
        Postgres backend's ``pool_timeout_seconds`` bound.

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.
        """
        if self._db is None:
            return False
        try:
            async with asyncio.timeout(self._config.health_timeout_seconds):
                async with self._db.execute("SELECT 1") as cursor:
                    row = await cursor.fetchone()
            healthy = row is not None
        except TimeoutError:
            logger.warning(
                PERSISTENCE_BACKEND_HEALTH_CHECK,
                healthy=False,
                error="health_check timed out",
                error_type="TimeoutError",
                timeout_seconds=self._config.health_timeout_seconds,
            )
            return False
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_BACKEND_HEALTH_CHECK,
                healthy=False,
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
            )
            return False
        logger.debug(PERSISTENCE_BACKEND_HEALTH_CHECK, healthy=healthy)
        return healthy

    async def migrate(self) -> None:
        """Apply pending schema migrations via yoyo-migrations.

        On failure the backend's repositories are reset so callers
        cannot reuse a half-initialised state machine (mirrors the
        postgres backend's pool-close-on-failure behaviour).

        Raises:
            PersistenceConnectionError: If not connected.
            MigrationError: If migration application fails.
        """
        async with self._lifecycle_lock:
            if self._db is None:
                msg = "Cannot migrate: not connected"
                logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
                raise PersistenceConnectionError(msg)
            db_url = to_sqlite_url(self._config.path)
            try:
                await migrate_apply(db_url)
            except BaseException:
                db = self._db
                if db is not None:
                    try:
                        await db.close()
                    except (sqlite3.Error, aiosqlite.Error, OSError) as cleanup_exc:
                        logger.warning(
                            PERSISTENCE_BACKEND_DISCONNECT_ERROR,
                            path=self._config.path,
                            error_type=type(cleanup_exc).__name__,
                            error=safe_error_description(cleanup_exc),
                            context="cleanup_after_migration_failure",
                        )
                self._clear_state()
                raise

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an active connection.

        Returns:
            ``True`` when the backend has an active connection, ``False`` otherwise.
        """
        return self._db is not None

    def build_lockouts(self, auth_config: AuthConfig) -> LockoutRepository:
        """Return the cached lockout repository (built once per connection).

        The lockout repo maintains a process-local in-memory cache
        (``_locked``) on the auth hot path.  Returning a fresh instance
        on every call would reset that cache and silently "unlock"
        every user.  The cache is cleared on ``disconnect`` via
        ``_clear_state``.  The backend's ``write_context`` is passed
        through so lockout transactions serialize with other
        repositories writing to the same aiosqlite connection.

        Returns:
            Result of type ``LockoutRepository``.
        """
        if self._lockouts is None:
            self._lockouts = SQLiteLockoutRepository(
                self.get_db(),
                auth_config,
                write_context=self.write_context,
            )
        return self._lockouts

    def build_escalations(
        self,
        *,
        notify_channel: str | None = None,  # noqa: ARG002
    ) -> EscalationQueueRepository:
        """Construct an escalation queue repository.

        ``notify_channel`` is ignored by SQLite (no cross-instance
        NOTIFY/LISTEN). The backend's ``write_context`` is passed
        through so escalation transactions serialize with other
        repositories writing to the same aiosqlite connection.

        Returns:
            Result of type ``EscalationQueueRepository``.
        """
        from synthorg.persistence.sqlite.escalation_repo import (  # noqa: PLC0415
            SQLiteEscalationRepository,
        )

        db = self.get_db()
        return SQLiteEscalationRepository(db, write_context=self.write_context)

    def build_ontology_versioning(
        self,
    ) -> VersioningService[EntityDefinition]:
        """Construct the ontology versioning service bound to this backend.

        Returns:
            Result of type ``VersioningService[EntityDefinition]``.
        """
        from synthorg.persistence.sqlite.ontology_versioning import (  # noqa: PLC0415
            create_ontology_versioning,
        )

        return create_ontology_versioning(
            self.get_db(),
            write_context=self.write_context,
        )

    async def get_setting(self, key: NotBlankStr) -> str | None:
        """Retrieve a setting value by key from the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected.

        Returns:
            The matching entity, or ``None`` when no row matches.
        """
        result = await self.settings.get((NotBlankStr("_system"), key))
        return result.value if result is not None else None

    async def set_setting(self, key: NotBlankStr, value: str) -> None:
        """Store a setting value (upsert) in the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected.
        """
        from synthorg.persistence.settings_protocol import SettingRow  # noqa: PLC0415

        updated_at = format_iso_utc(datetime.now(UTC))
        await self.settings.save(
            SettingRow(
                namespace=NotBlankStr("_system"),
                key=key,
                value=value,
                updated_at=updated_at,
            ),
        )
