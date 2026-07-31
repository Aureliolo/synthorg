"""Connection catalog service.

Central registry for external service connections.  Provides CRUD,
lookup, credential resolution, and health status management.

Behaviour is composed from cohesive mixins: cache + per-name locks
(``_cache.py``), credential resolution (``_credential_resolver.py``),
the create pipeline (``_create_pipeline.py``), the update pipeline
(``_update_pipeline.py``), and OAuth token rotation
(``_oauth_rotation.py``). This module keeps the orchestration: create,
update, delete, health, and lookup.
"""

import asyncio
from datetime import UTC, datetime
from typing import override
from uuid import uuid4

from synthorg.core.concurrency import RefcountedLockMap
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections._cache import ConnectionCacheMixin
from synthorg.integrations.connections._create_pipeline import ConnectionCreateMixin
from synthorg.integrations.connections._credential_resolver import (
    CredentialResolverMixin,
)
from synthorg.integrations.connections._oauth_rotation import OAuthRotationMixin
from synthorg.integrations.connections._update_pipeline import (
    _UNSET,
    ConnectionUpdateMixin,
    _UnsetType,
    materialise_update,
)
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionHealth,
    ConnectionStatus,
    ConnectionType,
    WebhookIngestState,
)
from synthorg.integrations.errors import (
    ConnectionNotFoundError,
    DuplicateConnectionError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    CONNECTION_CREATED,
    CONNECTION_DELETED,
    CONNECTION_DUPLICATE,
    CONNECTION_NOT_FOUND,
    CONNECTION_UPDATE_FAILED,
    CONNECTION_UPDATED,
    HEALTH_STATUS_TRANSITIONED,
    SECRET_DELETE_FAILED,
    SECRET_DELETED,
)
from synthorg.persistence.connection_protocol import (
    ConnectionRepository,
)
from synthorg.persistence.secret_backends.protocol import (
    SecretBackend,
)

logger = get_logger(__name__)


class ConnectionCatalog(
    ConnectionCacheMixin,
    CredentialResolverMixin,
    ConnectionCreateMixin,
    ConnectionUpdateMixin,
    OAuthRotationMixin,
):
    """Central registry for external service connections.

    Thread-safe via ``asyncio.Lock`` for cache invalidation.
    All writes go through the persistence layer; reads use an
    in-memory cache that is invalidated on mutation.

    Args:
        repository: Persistence repository for connections.
        secret_backend: Backend for credential storage.
    """

    def __init__(
        self,
        repository: ConnectionRepository,
        secret_backend: SecretBackend,
    ) -> None:
        self._repo = repository
        self._secret_backend = secret_backend
        self._cache: dict[str, Connection] = {}
        self._cache_lock = asyncio.Lock()
        self._cache_valid = False
        # Per-name mutation lock used to serialize create/update/
        # delete/rotate for a given connection. Prevents races that
        # would otherwise leave orphaned secrets or repo rows; the map
        # evicts a name's lock once idle so it stays bounded.
        self._name_locks: RefcountedLockMap[str] = RefcountedLockMap()

    async def create(  # noqa: PLR0913
        self,
        *,
        name: str,
        connection_type: ConnectionType,
        auth_method: str,
        credentials: dict[str, str],
        base_url: str | None = None,
        metadata: dict[str, str] | None = None,
        health_check_enabled: bool = True,
        webhook_receipt_retention_days: int | None = None,
        sensitive: bool = False,
        allowed_repos: tuple[str, ...] = (),
    ) -> Connection:
        """Create a new connection.

        Validates credentials via the type's authenticator, encrypts
        them via the secret backend, and persists the connection.

        Args:
            name: Unique connection name.
            connection_type: Service type.
            auth_method: How credentials are provided.
            credentials: Plaintext credentials (encrypted before storage).
            base_url: Optional base URL. A ``generic_http`` connection whose
                metadata names a vendor preset inherits that preset's
                endpoint when this is left unset.
            metadata: Optional user tags.
            health_check_enabled: Whether to probe health.
            webhook_receipt_retention_days: Optional per-connection override
                for the webhook-receipt retention window (days). ``None``
                falls back to the global default; ``0`` opts out of the
                cleanup sweep entirely.
            sensitive: Marks the connection sensitive so the governed
                external-access tool routes every call against it to
                human approval.
            allowed_repos: Least-privilege forge repository scope
                (``owner/repo`` entries, ``owner/*`` globs). Empty denies
                every repository (fail-closed).

        Returns:
            The persisted connection.

        Raises:
            DuplicateConnectionError: If name already exists.
            InvalidConnectionAuthError: If credentials are invalid.
        """
        async with self._name_lock(name):
            await self._ensure_cache()
            if name in self._cache:
                logger.warning(CONNECTION_DUPLICATE, connection_name=name)
                msg = f"Connection '{name}' already exists"
                raise DuplicateConnectionError(msg)

            resolved_base_url = self._resolve_base_url(
                connection_type,
                base_url,
                metadata,
            )
            self._validate_credentials_for_create(
                name,
                connection_type,
                credentials,
                resolved_base_url,
                auth_method,
            )
            secret_id = str(uuid4())
            connection = self._build_connection(
                name=name,
                connection_type=connection_type,
                auth_method=auth_method,
                base_url=resolved_base_url,
                secret_id=secret_id,
                metadata=metadata,
                health_check_enabled=health_check_enabled,
                webhook_receipt_retention_days=webhook_receipt_retention_days,
                sensitive=sensitive,
                allowed_repos=allowed_repos,
            )
            await self._store_secret(secret_id, credentials, connection_name=name)
            await self._persist_connection_with_cleanup(
                connection,
                secret_id=secret_id,
            )
            await self._invalidate_cache()
            logger.info(
                CONNECTION_CREATED,
                connection_name=name,
                connection_type=connection_type,
            )
            return connection

    @override
    async def get(self, name: str) -> Connection | None:
        """Retrieve a connection by name.

        Returns:
            The matching ``Connection``, or ``None`` when no connection
            with that name exists.
        """
        await self._ensure_cache()
        return self._cache.get(name)

    @override
    async def get_or_raise(self, name: str) -> Connection:
        """Retrieve a connection by name, or raise.

        Returns:
            The matching ``Connection``.

        Raises:
            ConnectionNotFoundError: If the connection does not exist.
        """
        conn = await self.get(name)
        if conn is None:
            # DEBUG, not WARNING: the raise is the signal, and every caller
            # for whom an absence is exceptional reports it with its own
            # context. A warning here would speak for all of them, including
            # the polls that ask whether an optional integration is
            # configured yet, for which "no" is the routine answer.
            logger.debug(CONNECTION_NOT_FOUND, connection_name=name)
            msg = f"Connection '{name}' not found"
            raise ConnectionNotFoundError(msg)
        return conn

    async def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List all connections, optionally paginated.

        ``limit=None`` (the default) preserves the unbounded behaviour
        existing callers rely on; when ``limit`` is set, ``offset`` is
        honoured and the cache snapshot is sliced consistently with
        the connection's natural insertion order in the cache.

        Returns:
            A tuple of all cached connections, sliced to the requested
            page window when ``limit`` is set.
        """
        await self._ensure_cache()
        snapshot = tuple(self._cache.values())
        if limit is None and offset == 0:
            return snapshot
        offset = max(0, offset)
        end = None if limit is None else offset + max(0, limit)
        return snapshot[offset:end]

    async def list_by_type(
        self,
        connection_type: ConnectionType,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List connections of a specific type, optionally paginated.

        See :meth:`list_all` for the limit/offset contract.

        Returns:
            A tuple of connections matching ``connection_type``, sliced
            to the requested page window when ``limit`` is set.
        """
        await self._ensure_cache()
        filtered = tuple(
            c for c in self._cache.values() if c.connection_type == connection_type
        )
        if limit is None and offset == 0:
            return filtered
        offset = max(0, offset)
        end = None if limit is None else offset + max(0, limit)
        return filtered[offset:end]

    async def count_all(self) -> int:
        """Return the unfiltered count of cached connections.

        Returns:
            The number of connections currently held in the cache.
        """
        await self._ensure_cache()
        return len(self._cache)

    async def count_by_type(self, connection_type: ConnectionType) -> int:
        """Return the count of cached connections of ``connection_type``.

        Returns:
            The number of cached connections whose ``connection_type``
            matches the given value.
        """
        await self._ensure_cache()
        return sum(
            1 for c in self._cache.values() if c.connection_type == connection_type
        )

    async def update(
        self,
        name: str,
        *,
        base_url: str | _UnsetType | None = _UNSET,
        metadata: dict[str, str] | _UnsetType | None = _UNSET,
        health_check_enabled: bool | _UnsetType | None = _UNSET,
        webhook_receipt_retention_days: int | _UnsetType | None = _UNSET,
        sensitive: bool | _UnsetType = _UNSET,
        allowed_repos: tuple[str, ...] | _UnsetType = _UNSET,
    ) -> Connection:
        """Update a connection's mutable fields.

        Each kwarg uses the ``_UNSET`` sentinel to distinguish "field
        omitted" from "field set to ``None``" (clear).  Callers that
        want a no-op pass nothing; callers that want to explicitly
        null out a value pass ``None``.  ``webhook_receipt_retention_days``
        follows the same semantic: ``None`` clears the per-connection
        override (falls back to the global default), an int sets the
        override, leaving unset keeps the existing stored value.

        Returns:
            The updated ``Connection`` row after persistence; the same
            unchanged row when no fields actually changed.

        Raises:
            ConnectionNotFoundError: If the connection does not exist.
            InvalidConnectionEndpointError: If the update moves a
                ``generic_http`` connection onto a vendor that supplies no
                endpoint of its own and names no replacement.
        """
        async with self._name_lock(name):
            existing = await self.get_or_raise(name)
            candidate = self._candidate_or_report(
                name,
                base_url=base_url,
                metadata=metadata,
                health_check_enabled=health_check_enabled,
                webhook_receipt_retention_days=webhook_receipt_retention_days,
                sensitive=sensitive,
                allowed_repos=allowed_repos,
            )
            self._repair_vendor_base_url(existing, candidate)
            updated = materialise_update(existing, candidate)
            if updated is None:
                return existing
            try:
                await self._repo.save(updated)
            except Exception as exc:
                reraise_critical(exc)
                # PATCH persistence failed; surface ``connection_name``
                # context before re-raising so the failure is
                # attributable in dashboards (the repo's own exception
                # only carries a row id). Error is redacted via
                # ``safe_error_description`` to keep secret-backend
                # internals out of the log sink.
                logger.warning(
                    CONNECTION_UPDATE_FAILED,
                    connection_name=name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            await self._invalidate_cache()
            logger.info(CONNECTION_UPDATED, connection_name=name)
            return updated

    async def update_health(
        self,
        name: str,
        *,
        status: ConnectionStatus,
        checked_at: datetime,
        detail: str | None = None,
        latency_ms: float | None = None,
        webhook_ingest: WebhookIngestState = WebhookIngestState.NOT_APPLICABLE,
        retry_after_seconds: float | None = None,
    ) -> Connection:
        """Update a connection's health status.

        The whole verdict is persisted, not just the headline: a reader that
        serves this snapshot instead of re-probing (the aggregate-health
        endpoint) must be able to say why a connection is failing, not merely
        that it is.

        Returns:
            The updated ``Connection`` row with the new health snapshot
            persisted.

        Raises:
            ConnectionNotFoundError: If the connection does not exist.
        """
        async with self._name_lock(name):
            existing = await self.get_or_raise(name)
            updated = existing.model_copy(
                update={
                    "health": ConnectionHealth(
                        status=status,
                        last_check_at=checked_at,
                        detail=detail,
                        latency_ms=latency_ms,
                        webhook_ingest=webhook_ingest,
                        retry_after_seconds=retry_after_seconds,
                    ),
                    "updated_at": datetime.now(UTC),
                },
            )
            await self._repo.save(updated)
            await self._invalidate_cache()
            # Log the transition only when it actually changed, so a
            # quiet health prober cycling the same status does not
            # flood the log stream.
            if existing.health.status != status:
                logger.info(
                    HEALTH_STATUS_TRANSITIONED,
                    connection_name=name,
                    previous_status=existing.health.status.value,
                    new_status=status.value,
                )
            return updated

    async def delete(self, name: str) -> None:
        """Delete a connection and its secrets.

        The repo row is removed first; secrets are only deleted
        after the repo deletion succeeds, so a failure during
        secret cleanup leaves the row already removed (and the
        orphaned secret is logged for follow-up).

        Raises:
            ConnectionNotFoundError: If the connection does not exist.
        """
        async with self._name_lock(name):
            existing = await self.get_or_raise(name)
            await self._repo.delete(name)
            # Drop the cache before deleting the secrets, not after: reads do
            # not take the name lock, so every await in the cleanup loop below
            # is a point where a concurrent reader would otherwise be handed a
            # cached connection whose secrets are already gone, and raise on
            # retrieval instead of reporting the connection as absent.
            await self._invalidate_cache()
            for ref in existing.secret_refs:
                try:
                    deleted = await self._secret_backend.delete(ref.secret_id)
                    if deleted:
                        logger.debug(
                            SECRET_DELETED,
                            connection_name=name,
                            secret_id=ref.secret_id,
                        )
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    # The connection delete itself succeeded; a stale
                    # secret cleanup failure is a secret-delete
                    # problem, not a connection-delete problem -- use
                    # the cleanup-scoped event so dashboards stop
                    # overcounting successful connection deletions
                    # when secret cleanup blows up. Error is redacted
                    # via ``safe_error_description`` to keep backend
                    # internals out of the log sink.
                    logger.warning(
                        SECRET_DELETE_FAILED,
                        connection_name=name,
                        secret_id=ref.secret_id,
                        note="failed_to_delete_secret_after_connection_delete",
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
            logger.info(CONNECTION_DELETED, connection_name=name)
