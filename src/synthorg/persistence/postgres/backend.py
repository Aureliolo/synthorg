"""Postgres persistence backend implementation.

Implements the ``PersistenceBackend`` protocol on top of psycopg 3 and
``psycopg_pool.AsyncConnectionPool``.  Repositories are instantiated
per-backend on ``connect()`` and receive the shared pool; each pool
checkout is an independent transaction, so this backend's
``write_context`` is a no-op rather than the in-process lock SQLite
acquires to serialize writes across its single connection.

The schema uses native Postgres types (JSONB, TIMESTAMPTZ, BIGINT,
BOOLEAN) -- see ``src/synthorg/persistence/postgres/schema.sql``.  At
the Python level, the protocol surface is identical to the SQLite
backend: callers get Pydantic models back either way.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.auth.config import AuthConfig
from synthorg.core.persistence_errors import PersistenceConnectionError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.persistence.backend import (
    PERSISTENCE_BACKEND_NOT_CONNECTED,
)
from synthorg.ontology.models import EntityDefinition
from synthorg.persistence._shared import format_iso_utc
from synthorg.persistence.auth_protocol import (
    LockoutRepository,
)
from synthorg.persistence.config import PostgresConfig
from synthorg.persistence.escalation_protocol import EscalationQueueRepository
from synthorg.persistence.postgres._repository_wiring import (
    _PostgresRepositoryWiring,
)
from synthorg.persistence.postgres.backend_connection import PostgresConnectionMixin
from synthorg.persistence.postgres.backend_migration import PostgresMigrationMixin
from synthorg.persistence.postgres.lockout_repo import (
    PostgresLockoutRepository,
)
from synthorg.persistence.settings_protocol import SettingRow
from synthorg.versioning.service import VersioningService

logger = get_logger(__name__)


class PostgresPersistenceBackend(
    _PostgresRepositoryWiring,
    PostgresConnectionMixin,
    PostgresMigrationMixin,
):
    """Postgres implementation of the ``PersistenceBackend`` protocol.

    Uses a ``psycopg_pool.AsyncConnectionPool`` for connection
    management.  Each repository method acquires a connection from the
    pool for the duration of its critical section, so writes are
    isolated per-connection transaction.  There is no shared write
    lock -- unlike SQLite, Postgres per-connection transactions do not
    share a single in-process connection.

    Args:
        config: Postgres-specific configuration.
    """

    def __init__(self, config: PostgresConfig) -> None:
        self._config = config
        self._lifecycle_lock = asyncio.Lock()
        self._clear_state()

    def get_db(self) -> AsyncConnectionPool:
        """Return the shared connection pool.

        Raises:
            PersistenceConnectionError: If not yet connected.

        Returns:
            The active connection pool (raises if not connected).
        """
        if self._pool is None:
            msg = "Postgres backend not connected"
            logger.warning(PERSISTENCE_BACKEND_NOT_CONNECTED, error=msg)
            raise PersistenceConnectionError(msg)
        return self._pool

    @asynccontextmanager
    async def write_context(self) -> AsyncIterator[None]:
        """No-op for Postgres.

        Each repository checks out its own connection from the async
        pool; transactions on different connections cannot interleave
        at the statement level. Implementing the protocol method as a
        no-op keeps the cross-backend interface honest and lets
        callers write ``async with backend.write_context()`` without
        backend-specific branching.
        """
        yield

    @property
    def is_connected(self) -> bool:
        """Whether the backend has an open pool.

        Returns:
            ``True`` when the backend has an active connection, ``False`` otherwise.
        """
        return self._pool is not None

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return NotBlankStr("postgres")

    @property
    def kind(self) -> Literal["sqlite", "postgres"]:
        """Return the backend discriminator (``"postgres"``).

        Returns:
            Result of type ``Literal['sqlite', 'postgres']``.
        """
        return "postgres"

    @property
    def config(self) -> PostgresConfig:
        """Public read-only view of the backend's Postgres config.

        Exposed so callers needing the connection details (the
        backup-handler factory) do not have to reach for the
        private ``_config`` attribute.

        Returns:
            Result of type ``PostgresConfig``.
        """
        return self._config

    def build_lockouts(self, auth_config: AuthConfig) -> LockoutRepository:
        """Construct a lockout repository using this backend's pool.

        Returns:
            Result of type ``LockoutRepository``.
        """
        pool = self.get_db()
        return PostgresLockoutRepository(pool, auth_config)

    def build_escalations(
        self,
        *,
        notify_channel: str | None = None,
    ) -> EscalationQueueRepository:
        """Construct an escalation queue repository on the shared pool.

        ``notify_channel`` enables cross-instance pg_notify publishing
        when the escalation subsystem has enabled it.

        Returns:
            Result of type ``EscalationQueueRepository``.
        """
        from synthorg.persistence.postgres.escalation_repo import (  # noqa: PLC0415
            PostgresEscalationRepository,
        )

        pool = self.get_db()
        return PostgresEscalationRepository(pool, notify_channel=notify_channel)

    def build_ontology_versioning(
        self,
    ) -> VersioningService[EntityDefinition]:
        """Construct the ontology versioning service bound to this backend.

        Returns:
            Result of type ``VersioningService[EntityDefinition]``.
        """
        from synthorg.persistence.postgres.ontology_versioning import (  # noqa: PLC0415
            create_postgres_ontology_versioning,
        )

        return create_postgres_ontology_versioning(self.get_db())

    async def get_setting(self, key: NotBlankStr) -> str | None:
        """Retrieve a setting value by key from the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected or settings
                repository is not yet ported.

        Returns:
            The setting value as ``str``, or ``None`` when no row matches.
        """
        entity = await self.settings.get((NotBlankStr("_system"), key))
        return entity.value if entity is not None else None

    async def set_setting(self, key: NotBlankStr, value: str) -> None:
        """Store a setting value (upsert) in the ``_system`` namespace.

        Delegates to ``self.settings`` (the ``SettingsRepository``).

        Raises:
            PersistenceConnectionError: If not connected or settings
                repository is not yet ported.
        """
        updated_at = datetime.now(UTC)
        entity = SettingRow(
            namespace=NotBlankStr("_system"),
            key=key,
            value=value,
            updated_at=format_iso_utc(updated_at),
        )
        await self.settings.save(entity)


# Public re-export for convenience.
__all__ = ["PostgresPersistenceBackend", "dict_row"]
