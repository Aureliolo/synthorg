"""OAuth token-rotation behaviour for :class:`ConnectionCatalog`.

The rotation path (store fresh tokens, allocate a new secret, persist the
connection, then best-effort delete the superseded secrets) is a cohesive,
secret-bearing slice of the catalog. It lives in its own mixin so the main
catalog module stays focused on CRUD + lookup + credential resolution.

The mixin reaches back into the host catalog for shared collaborators
(``_repo``, ``_secret_backend``) and helpers (``_name_lock``,
``get_or_raise``, ``_resolve_credentials_for``, ``_store_secret``,
``_invalidate_cache``); the ``TYPE_CHECKING`` block below declares that
surface so ``mypy`` type-checks the mixin in isolation.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import Connection, SecretRef
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    OAUTH_TOKEN_EXCHANGE_FAILED,
    OAUTH_TOKEN_EXCHANGED,
    SECRET_DELETE_FAILED,
    SECRET_DELETED,
)
from synthorg.observability.events.security import SECURITY_CONNECTION_UPDATED

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from synthorg.persistence.connection_protocol import ConnectionRepository
    from synthorg.persistence.secret_backends.protocol import SecretBackend

logger = get_logger(__name__)


class OAuthRotationMixin:
    """OAuth token-rotation methods mixed into :class:`ConnectionCatalog`."""

    if TYPE_CHECKING:
        _repo: ConnectionRepository
        _secret_backend: SecretBackend

        def _name_lock(self, name: str) -> AbstractAsyncContextManager[None]:
            """Return the per-connection lock CM (provided by the host class)."""
            ...

        async def get_or_raise(self, name: str) -> Connection:
            """Load a connection or raise (provided by the host class)."""
            ...

        async def _resolve_credentials_for(
            self,
            conn: Connection,
        ) -> dict[str, str]:
            """Merge a connection's credentials (provided by the host class)."""
            ...

        async def _store_secret(
            self,
            secret_id: str,
            payload: dict[str, str],
            *,
            connection_name: str,
            failure_event: str,
        ) -> None:
            """Write a secret blob to the backend (provided by the host class)."""
            ...

        async def _invalidate_cache(self) -> None:
            """Drop the cached connection snapshot (provided by the host class)."""
            ...

    async def store_oauth_tokens(
        self,
        name: str,
        *,
        access_token: str,
        refresh_token: str | None = None,
    ) -> Connection:
        """Persist OAuth access/refresh tokens via the secret backend.

        Merges the tokens into the connection's existing credential
        blob (so token_url, client_id, client_secret etc. remain
        available) and collapses ``secret_refs`` to a single fresh
        ``SecretRef`` pointing at the merged payload. Any
        previously-referenced secrets are deleted from the backend
        so ``get_credentials`` cannot reintroduce stale keys on
        the next resolve.

        Returns:
            The updated ``Connection`` row with a single fresh
            ``SecretRef`` pointing at the merged token payload.

        Raises:
            ConnectionNotFoundError: If the connection does not exist.
        """
        async with self._name_lock(name):
            # Load the connection once and share it across the
            # credential merge + persist paths.
            conn = await self.get_or_raise(name)
            existing = await self._resolve_credentials_for(conn)
            merged = dict(existing)
            merged["access_token"] = access_token
            if refresh_token is not None:
                merged["refresh_token"] = refresh_token

            new_secret_id, updated = await self._stage_oauth_secret_rotation(
                conn,
                merged,
            )
            await self._persist_oauth_rotation(updated, new_secret_id, name)
            await self._invalidate_cache()
            await self._cleanup_stale_oauth_secrets(conn.secret_refs, name)
            logger.info(
                OAUTH_TOKEN_EXCHANGED,
                connection_name=name,
                has_refresh=refresh_token is not None,
            )
            # Sign the credential write into the audit chain: storing live
            # OAuth bearer tokens is equivalent in impact to a REST
            # connection update (which signs ``SECURITY_CONNECTION_UPDATED``),
            # and this callback path is unauthenticated, making audit
            # coverage more critical. ``principal="system"`` marks the
            # provider-driven callback actor.
            logger.info(
                SECURITY_CONNECTION_UPDATED,
                principal="system",
                resource=f"connection:{name}",
                action_type="oauth_token_rotation",
                connection_name=name,
            )
            return updated

    async def _stage_oauth_secret_rotation(
        self,
        conn: Connection,
        merged: dict[str, str],
    ) -> tuple[str, Connection]:
        """Write the merged secret blob and stage the updated connection.

        Always allocates a fresh secret id and collapses ``secret_refs``
        to exactly that one ref.  Writing back into an existing ref
        would leave sibling refs pointing at stale credential slices,
        and ``get_credentials`` merges them in order so old values
        could shadow the fresh token on the next resolve.

        Returns:
            A ``(new_secret_id, updated_connection)`` tuple: the
            freshly-allocated secret UUID and the connection with its
            ``secret_refs`` collapsed to that single ref.
        """
        new_secret_id = str(uuid4())
        # Route through ``_store_secret`` so a backend-store failure
        # carries ``connection_name`` / ``secret_id`` context under the
        # OAuth-scoped event before bubbling to the caller.
        await self._store_secret(
            new_secret_id,
            merged,
            connection_name=conn.name,
            failure_event=OAUTH_TOKEN_EXCHANGE_FAILED,
        )
        ref = SecretRef(
            secret_id=NotBlankStr(new_secret_id),
            backend=NotBlankStr(self._secret_backend.backend_name),
        )
        updated = conn.model_copy(
            update={
                "secret_refs": (ref,),
                "updated_at": datetime.now(UTC),
            }
        )
        return new_secret_id, updated

    async def _persist_oauth_rotation(
        self,
        updated: Connection,
        new_secret_id: str,
        name: str,
    ) -> None:
        """Persist the rotated connection; delete the new secret on failure.

        Logs use ``safe_error_description`` rather than raw tracebacks
        because the OAuth-token path is secret-bearing -- tracebacks
        on this code path can leak token / backend internals into the
        log sink.
        """
        try:
            await self._repo.save(updated)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                OAUTH_TOKEN_EXCHANGE_FAILED,
                connection_name=name,
                note="repo_save_failed_deleting_orphaned_oauth_secret",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            try:
                await self._secret_backend.delete(new_secret_id)
            except Exception as cleanup_exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(cleanup_exc)
                logger.warning(
                    OAUTH_TOKEN_EXCHANGE_FAILED,
                    connection_name=name,
                    secret_id=new_secret_id,
                    error_context=(
                        "rollback delete failed; manual cleanup "
                        "required for orphaned OAuth secret"
                    ),
                    error_type=type(cleanup_exc).__name__,
                    error=safe_error_description(cleanup_exc),
                )
            raise

    async def _cleanup_stale_oauth_secrets(
        self,
        old_refs: tuple[SecretRef, ...],
        name: str,
    ) -> None:
        """Best-effort delete the previously-referenced OAuth secrets.

        Repo save has already succeeded so failures here log but do
        not re-raise -- a single stale secret should not abort the
        whole rotation.  Stale-secret cleanup failure is a
        ``SECRET_DELETE_FAILED`` event, not a token-exchange failure.
        """
        for old_ref in old_refs:
            try:
                deleted = await self._secret_backend.delete(old_ref.secret_id)
                if deleted:
                    logger.debug(
                        SECRET_DELETED,
                        connection_name=name,
                        secret_id=old_ref.secret_id,
                    )
            except Exception as del_exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(del_exc)
                logger.warning(
                    SECRET_DELETE_FAILED,
                    connection_name=name,
                    secret_id=old_ref.secret_id,
                    note="failed_to_delete_stale_secret_after_rotation",
                    error_type=type(del_exc).__name__,
                    error=safe_error_description(del_exc),
                )
