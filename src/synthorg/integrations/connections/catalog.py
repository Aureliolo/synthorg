"""Connection catalog service.

Central registry for external service connections.  Provides CRUD,
lookup, credential resolution, and health status management.
"""

import asyncio
import copy
import json
from datetime import UTC, datetime
from typing import override
from uuid import uuid4

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections._oauth_rotation import OAuthRotationMixin
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
    SecretRef,
)
from synthorg.integrations.connections.types import get_authenticator
from synthorg.integrations.errors import (
    ConnectionNotFoundError,
    DuplicateConnectionError,
    InvalidConnectionAuthError,
    SecretRetrievalError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    CONNECTION_CREATE_FAILED,
    CONNECTION_CREATED,
    CONNECTION_DELETED,
    CONNECTION_DUPLICATE,
    CONNECTION_NOT_FOUND,
    CONNECTION_UPDATE_FAILED,
    CONNECTION_UPDATED,
    CONNECTION_VALIDATION_FAILED,
    HEALTH_STATUS_TRANSITIONED,
    SECRET_DELETE_FAILED,
    SECRET_DELETED,
    SECRET_RETRIEVAL_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import paginate
from synthorg.persistence.connection_protocol import (
    ConnectionRepository,
)
from synthorg.persistence.secret_backends.protocol import (
    SecretBackend,
)

logger = get_logger(__name__)


class _UnsetType:
    """Sentinel type for omitted PATCH fields.

    Defining a dedicated type lets ``mypy`` narrow ``value is _UNSET``
    properly; the previous ``| object`` annotation accepted any value
    and defeated narrowing in strict mode.
    """


_UNSET = _UnsetType()
"""Sentinel value to distinguish 'not provided' from None."""


class ConnectionCatalog(OAuthRotationMixin):
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
        # would otherwise leave orphaned secrets or repo rows.
        self._name_locks: dict[str, asyncio.Lock] = {}
        self._name_locks_lock = asyncio.Lock()

    async def rebind_repository(self, repository: ConnectionRepository) -> None:
        """Swap the underlying repository and invalidate the cache.

        Used by the API lifecycle hook to graduate the catalog from the
        startup-window ``InMemoryConnectionRepository`` stub (installed
        before ``persistence.connect()`` succeeds) to the real
        backend-bound repository once persistence is live.

        Args:
            repository: The newly-available persistence-backed
                ``ConnectionRepository`` to take over from this point on.
        """
        # Hold ``_cache_lock`` for the swap so a concurrent
        # ``_ensure_cache`` cannot observe a half-swapped state where
        # ``self._repo`` is already the new backend but ``self._cache``
        # still carries entries seeded from the in-memory stub.
        async with self._cache_lock:
            self._repo = repository
            self._cache = {}
            self._cache_valid = False

    async def _ensure_cache(self) -> None:
        """Populate the cache from persistence if invalid."""
        if self._cache_valid:
            return
        async with self._cache_lock:
            # Re-check under lock (double-checked locking)
            if not self._cache_valid:
                # The catalog needs every persisted connection to
                # satisfy synchronous ``by_name`` lookups, so page
                # through the full set: a single capped read would
                # silently drop connections past the backend page cap.
                collected: list[Connection] = []
                async for page in paginate(
                    lambda limit, offset: self._repo.list_items(
                        limit=limit, offset=offset
                    ),
                    page_size=DEFAULT_PAGE_SIZE,
                ):
                    collected.extend(page)
                self._cache = {c.name: c for c in collected}
                self._cache_valid = True

    @override
    def _invalidate_cache(self) -> None:
        self._cache_valid = False

    def get_cached(self, name: str) -> Connection | None:
        """Return the cached connection for ``name`` without populating.

        Synchronous peek into the in-memory cache; returns ``None`` when
        the cache has not been primed yet or the name is unknown. Use
        when callers prefer a best-effort read over forcing a
        repository fetch (e.g. boot-time rate-limit coordinators).
        """
        if not self._cache_valid:
            return None
        return self._cache.get(name)

    @override
    async def _lock_for(self, name: str) -> asyncio.Lock:
        """Return (or create) the mutation lock for a connection name."""
        async with self._name_locks_lock:
            lock = self._name_locks.get(name)
            if lock is None:
                lock = asyncio.Lock()
                self._name_locks[name] = lock
            return lock

    def _validate_credentials_for_create(
        self,
        name: str,
        connection_type: ConnectionType,
        credentials: dict[str, str],
    ) -> None:
        """Validate credentials via the type's authenticator before persist."""
        authenticator = get_authenticator(connection_type)
        try:
            authenticator.validate_credentials(credentials)
        except InvalidConnectionAuthError:
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_name=name,
                connection_type=connection_type,
            )
            raise

    def _build_connection(  # noqa: PLR0913
        self,
        *,
        name: str,
        connection_type: ConnectionType,
        auth_method: str,
        base_url: str | None,
        secret_id: str,
        metadata: dict[str, str] | None,
        health_check_enabled: bool,
        webhook_receipt_retention_days: int | None,
        sensitive: bool = False,
    ) -> Connection:
        """Build and validate the ``Connection`` model BEFORE secret writes.

        ``NotBlankStr`` rejections, ``AuthMethod`` rejections, and
        Pydantic ``@model_validator`` failures are caught here so we
        never leave an orphaned secret behind with no row to clean it
        up from.
        """
        secret_ref = SecretRef(
            secret_id=NotBlankStr(secret_id),
            backend=NotBlankStr(self._secret_backend.backend_name),
        )
        now = datetime.now(UTC)
        try:
            return Connection(
                name=NotBlankStr(name),
                connection_type=connection_type,
                auth_method=AuthMethod(auth_method),
                base_url=NotBlankStr(base_url) if base_url else None,
                secret_refs=(secret_ref,),
                health_check_enabled=health_check_enabled,
                metadata=metadata or {},
                webhook_receipt_retention_days=webhook_receipt_retention_days,
                sensitive=sensitive,
                created_at=now,
                updated_at=now,
            )
        except Exception as exc:
            reraise_critical(exc)
            # Surface ``connection_name`` context on model-construction
            # failures.  Without this the resulting 500 carries the
            # exception's raw message but no resource attribution.
            logger.warning(
                CONNECTION_VALIDATION_FAILED,
                connection_name=name,
                connection_type=connection_type,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    @override
    async def _store_secret(
        self,
        secret_id: str,
        credentials: dict[str, str],
        *,
        connection_name: str,
        failure_event: str = CONNECTION_CREATE_FAILED,
    ) -> None:
        """Store credentials via the secret backend with structured error log.

        ``failure_event`` lets callers route store-failure logs to the
        right operation taxonomy (``CONNECTION_CREATE_FAILED`` for the
        create path, ``OAUTH_TOKEN_EXCHANGE_FAILED`` for the rotation
        path) so dashboards keyed by event type stay consistent.
        """
        try:
            await self._secret_backend.store(
                secret_id,
                json.dumps(credentials).encode("utf-8"),
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                failure_event,
                connection_name=connection_name,
                secret_id=secret_id,
                note="secret_backend_store_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def _persist_connection_with_cleanup(
        self,
        connection: Connection,
        *,
        secret_id: str,
    ) -> None:
        """Persist the connection row; on failure, delete the orphaned secret.

        Uses a structured warning with a redacted error rather than
        ``logger.exception`` so a raw traceback cannot leak repo /
        secret-backend internals into the log sink.
        """
        try:
            await self._repo.save(connection)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                CONNECTION_CREATE_FAILED,
                connection_name=connection.name,
                note="repo_save_failed_deleting_orphaned_secret",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            try:
                await self._secret_backend.delete(secret_id)
            except Exception as cleanup_exc:
                reraise_critical(cleanup_exc)
                logger.warning(
                    CONNECTION_CREATE_FAILED,
                    connection_name=connection.name,
                    secret_id=secret_id,
                    note="rollback_delete_failed_manual_cleanup_required",
                    error_type=type(cleanup_exc).__name__,
                    error=safe_error_description(cleanup_exc),
                )
            raise

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
    ) -> Connection:
        """Create a new connection.

        Validates credentials via the type's authenticator, encrypts
        them via the secret backend, and persists the connection.

        Args:
            name: Unique connection name.
            connection_type: Service type.
            auth_method: How credentials are provided.
            credentials: Plaintext credentials (encrypted before storage).
            base_url: Optional base URL.
            metadata: Optional user tags.
            health_check_enabled: Whether to probe health.
            webhook_receipt_retention_days: Optional per-connection override
                for the webhook-receipt retention window (days). ``None``
                falls back to the global default; ``0`` opts out of the
                cleanup sweep entirely.
            sensitive: Marks the connection sensitive so the governed
                external-access tool routes every call against it to
                human approval.

        Returns:
            The persisted connection.

        Raises:
            DuplicateConnectionError: If name already exists.
            InvalidConnectionAuthError: If credentials are invalid.
        """
        lock = await self._lock_for(name)
        async with lock:
            await self._ensure_cache()
            if name in self._cache:
                logger.warning(CONNECTION_DUPLICATE, connection_name=name)
                msg = f"Connection '{name}' already exists"
                raise DuplicateConnectionError(msg)

            self._validate_credentials_for_create(
                name,
                connection_type,
                credentials,
            )
            secret_id = str(uuid4())
            connection = self._build_connection(
                name=name,
                connection_type=connection_type,
                auth_method=auth_method,
                base_url=base_url,
                secret_id=secret_id,
                metadata=metadata,
                health_check_enabled=health_check_enabled,
                webhook_receipt_retention_days=webhook_receipt_retention_days,
                sensitive=sensitive,
            )
            await self._store_secret(secret_id, credentials, connection_name=name)
            await self._persist_connection_with_cleanup(
                connection,
                secret_id=secret_id,
            )
            self._invalidate_cache()
            logger.info(
                CONNECTION_CREATED,
                connection_name=name,
                connection_type=connection_type,
            )
            return connection

    async def get(self, name: str) -> Connection | None:
        """Retrieve a connection by name."""
        await self._ensure_cache()
        return self._cache.get(name)

    @override
    async def get_or_raise(self, name: str) -> Connection:
        """Retrieve a connection by name, or raise.

        Raises:
            ConnectionNotFoundError: If the connection does not exist.
        """
        conn = await self.get(name)
        if conn is None:
            logger.warning(CONNECTION_NOT_FOUND, connection_name=name)
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
        """Return the unfiltered count of cached connections."""
        await self._ensure_cache()
        return len(self._cache)

    async def count_by_type(self, connection_type: ConnectionType) -> int:
        """Return the count of cached connections of ``connection_type``."""
        await self._ensure_cache()
        return sum(
            1 for c in self._cache.values() if c.connection_type == connection_type
        )

    def _build_update_candidate(
        self,
        *,
        base_url: str | None | _UnsetType,
        metadata: dict[str, str] | None | _UnsetType,
        health_check_enabled: bool | None | _UnsetType,
        webhook_receipt_retention_days: int | None | _UnsetType,
        sensitive: bool | _UnsetType,
    ) -> dict[str, object]:
        """Compose the PATCH candidate dict, normalising explicit nulls.

        Extracted from :meth:`update` so the per-field ``_UNSET`` /
        ``None`` normalisation does not push the caller over the
        cyclomatic-complexity budget.  The returned mapping is the
        proposed update *before* the idempotent-no-op filter
        compares it against the existing row.
        """
        candidate: dict[str, object] = {}
        if base_url is not _UNSET:
            candidate["base_url"] = NotBlankStr(base_url) if base_url else None
        if metadata is not _UNSET:
            # Normalise explicit ``null`` to the canonical empty
            # mapping used by ``create()``; ``model_copy`` does
            # not re-run validators so a raw ``None`` would
            # persist as ``metadata=None`` on the row even
            # though ``Connection.metadata`` is typed
            # ``dict[str, str]``.
            candidate["metadata"] = metadata if metadata is not None else {}
        if health_check_enabled is not _UNSET:
            # Same reasoning as ``metadata`` above;
            # ``create()`` always materialises
            # ``health_check_enabled=True`` so an explicit-null
            # clear normalises to the same default.
            candidate["health_check_enabled"] = (
                health_check_enabled if health_check_enabled is not None else True
            )
        if webhook_receipt_retention_days is not _UNSET:
            # ``None`` is a meaningful value here -- it clears the
            # per-connection override and falls back to the global
            # default.  Pass through verbatim.
            candidate["webhook_receipt_retention_days"] = webhook_receipt_retention_days
        if sensitive is not _UNSET:
            candidate["sensitive"] = sensitive
        return candidate

    async def update(  # noqa: PLR0913 -- one kwarg per independently-patchable field
        self,
        name: str,
        *,
        base_url: str | None | _UnsetType = _UNSET,
        metadata: dict[str, str] | None | _UnsetType = _UNSET,
        health_check_enabled: bool | None | _UnsetType = _UNSET,
        webhook_receipt_retention_days: int | None | _UnsetType = _UNSET,
        sensitive: bool | _UnsetType = _UNSET,
    ) -> Connection:
        """Update a connection's mutable fields.

        Each kwarg uses the ``_UNSET`` sentinel to distinguish "field
        omitted" from "field set to ``None``" (clear).  Callers that
        want a no-op pass nothing; callers that want to explicitly
        null out a value pass ``None``.  ``webhook_receipt_retention_days``
        follows the same semantic: ``None`` clears the per-connection
        override (falls back to the global default), an int sets the
        override, leaving unset keeps the existing stored value.

        Raises:
            ConnectionNotFoundError: If the connection does not exist.
        """
        lock = await self._lock_for(name)
        async with lock:
            existing = await self.get_or_raise(name)
            # Build candidate updates without seeding ``updated_at`` --
            # an unchanged PATCH should be a no-op so we can skip
            # ``save`` and the ``CONNECTION_UPDATED`` audit emit.
            try:
                candidate = self._build_update_candidate(
                    base_url=base_url,
                    metadata=metadata,
                    health_check_enabled=health_check_enabled,
                    webhook_receipt_retention_days=webhook_receipt_retention_days,
                    sensitive=sensitive,
                )
            except Exception as exc:
                reraise_critical(exc)
                # ``NotBlankStr`` rejections (e.g. caller passed an
                # empty ``base_url``) currently bubble with no resource
                # attribution.  Surface ``connection_name`` + the input
                # field set before re-raising so the audit log can
                # explain WHICH PATCH was rejected and why.
                logger.warning(
                    CONNECTION_VALIDATION_FAILED,
                    connection_name=name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise
            # Drop fields whose candidate value matches the persisted
            # value -- otherwise an idempotent PATCH would still bump
            # ``updated_at`` and emit a phantom ``CONNECTION_UPDATED``
            # audit row.
            real_updates = {
                key: value
                for key, value in candidate.items()
                if getattr(existing, key) != value
            }
            if not real_updates:
                # No-op PATCH; return the existing row unchanged.
                return existing
            real_updates["updated_at"] = datetime.now(UTC)
            # ``model_copy(update=...)`` skips ``@model_validator``s, so
            # any nested mutable container we pass in (here ``metadata``)
            # would leak shared references to callers post-construction.
            # Deep-copy on the way in so the persisted row owns its own
            # mapping; matches the create-path's defensive deepcopy.
            if "metadata" in real_updates:
                real_updates["metadata"] = copy.deepcopy(real_updates["metadata"])
            updated = existing.model_copy(update=real_updates)
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
            self._invalidate_cache()
            logger.info(CONNECTION_UPDATED, connection_name=name)
            return updated

    async def update_health(
        self,
        name: str,
        *,
        status: ConnectionStatus,
        checked_at: datetime,
    ) -> Connection:
        """Update a connection's health status.

        Raises:
            ConnectionNotFoundError: If the connection does not exist.
        """
        lock = await self._lock_for(name)
        async with lock:
            existing = await self.get_or_raise(name)
            updated = existing.model_copy(
                update={
                    "health_status": status,
                    "last_health_check_at": checked_at,
                    "updated_at": datetime.now(UTC),
                },
            )
            await self._repo.save(updated)
            self._invalidate_cache()
            # Log the transition only when it actually changed, so a
            # quiet health prober cycling the same status does not
            # flood the log stream.
            if existing.health_status != status:
                logger.info(
                    HEALTH_STATUS_TRANSITIONED,
                    connection_name=name,
                    previous_status=existing.health_status.value,
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
        lock = await self._lock_for(name)
        async with lock:
            existing = await self.get_or_raise(name)
            await self._repo.delete(name)
            for ref in existing.secret_refs:
                try:
                    deleted = await self._secret_backend.delete(ref.secret_id)
                    if deleted:
                        logger.debug(
                            SECRET_DELETED,
                            connection_name=name,
                            secret_id=ref.secret_id,
                        )
                except Exception as exc:
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
            self._invalidate_cache()
            logger.info(CONNECTION_DELETED, connection_name=name)

    async def get_credentials(self, name: str) -> dict[str, str]:
        """Retrieve decrypted credentials for a connection.

        Resolves all ``SecretRef`` entries and returns the merged
        credential dict.

        Raises:
            ConnectionNotFoundError: If the connection does not exist.
            SecretRetrievalError: If a referenced secret is missing
                or cannot be decoded.
        """
        conn = await self.get_or_raise(name)
        return await self._resolve_credentials_for(conn)

    @override
    async def _resolve_credentials_for(
        self,
        conn: Connection,
    ) -> dict[str, str]:
        """Decrypt and merge credentials for a pre-loaded ``Connection``.

        Extracted from :meth:`get_credentials` so callers that have
        already loaded the connection under a lock can reuse the
        merge logic without hitting the cache a second time.
        """
        name = conn.name
        merged: dict[str, str] = {}
        for ref in conn.secret_refs:
            raw = await self._secret_backend.retrieve(ref.secret_id)
            if raw is None:
                logger.warning(
                    SECRET_RETRIEVAL_FAILED,
                    connection_name=name,
                    secret_id=ref.secret_id,
                    error="secret not found",
                )
                msg = (
                    f"Secret '{ref.secret_id}' for connection "
                    f"'{name}' not found in backend"
                )
                raise SecretRetrievalError(msg)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                # Raw exception messages on this code path may contain
                # secret material (the malformed payload was the
                # connection's stored secret); route the description
                # through ``safe_error_description`` so the credential
                # scrubber masks any embedded tokens before logging.
                logger.warning(
                    SECRET_RETRIEVAL_FAILED,
                    connection_name=name,
                    secret_id=ref.secret_id,
                    note="malformed_secret",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                msg = f"Secret '{ref.secret_id}' for connection '{name}' is malformed"
                raise SecretRetrievalError(msg) from exc
            if not isinstance(data, dict):
                logger.warning(
                    SECRET_RETRIEVAL_FAILED,
                    connection_name=name,
                    secret_id=ref.secret_id,
                    error="secret payload is not a dict",
                )
                msg = (
                    f"Secret '{ref.secret_id}' for connection "
                    f"'{name}' is not a credential dict"
                )
                raise SecretRetrievalError(msg)
            if not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in data.items()
            ):
                logger.warning(
                    SECRET_RETRIEVAL_FAILED,
                    connection_name=name,
                    secret_id=ref.secret_id,
                    error="secret payload contains non-string entries",
                )
                msg = (
                    f"Secret '{ref.secret_id}' for connection "
                    f"'{name}' contains non-string credential entries"
                )
                raise SecretRetrievalError(msg)
            merged.update(data)
        return copy.deepcopy(merged)
