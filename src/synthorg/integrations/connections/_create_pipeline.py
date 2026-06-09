"""Create-path helpers for :class:`ConnectionCatalog`.

Pre-persist validation, model construction, secret writes, and the
persist-with-orphan-cleanup step live in this mixin so the main catalog
module stays focused on orchestration. ``_store_secret`` is shared with
the OAuth-rotation path. The mixin reaches back into the host catalog for
``_repo`` and ``_secret_backend``; the ``TYPE_CHECKING`` block declares
that surface so ``mypy`` type-checks the mixin in isolation.
"""

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
    SecretRef,
)
from synthorg.integrations.connections.types import get_authenticator
from synthorg.integrations.errors import InvalidConnectionAuthError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    CONNECTION_CREATE_FAILED,
    CONNECTION_VALIDATION_FAILED,
)

if TYPE_CHECKING:
    from synthorg.persistence.connection_protocol import ConnectionRepository
    from synthorg.persistence.secret_backends.protocol import SecretBackend

logger = get_logger(__name__)


class ConnectionCreateMixin:
    """Create-path helper methods mixed into :class:`ConnectionCatalog`."""

    if TYPE_CHECKING:
        _repo: ConnectionRepository
        _secret_backend: SecretBackend

    def _validate_credentials_for_create(
        self,
        name: str,
        connection_type: ConnectionType,
        credentials: dict[str, str],
    ) -> None:
        """Validate credentials via the type's authenticator before persist.

        Args:
            name: Connection name (for error attribution).
            connection_type: Service type whose authenticator validates.
            credentials: Plaintext credentials to validate.

        Raises:
            InvalidConnectionAuthError: If the type's authenticator
                rejects the supplied credentials.
        """
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

        Returns:
            The fully constructed and validated ``Connection`` model,
            ready for secret write and persistence.
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

        Args:
            secret_id: Allocated secret identifier.
            credentials: Plaintext credentials to encrypt and store.
            connection_name: Owning connection (for error attribution).
            failure_event: Event constant logged on a store failure.
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

        Args:
            connection: The validated connection to persist.
            secret_id: Secret to clean up if the repo save fails.
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
