"""Credential-resolution behaviour for :class:`ConnectionCatalog`.

Decrypting and merging a connection's ``SecretRef`` blobs is a cohesive,
secret-bearing slice of the catalog. It lives in its own mixin so the
main catalog module stays focused on CRUD + lookup. The mixin reaches
back into the host catalog for ``_secret_backend`` and ``get_or_raise``;
the ``TYPE_CHECKING`` block declares that surface so ``mypy`` type-checks
the mixin in isolation.
"""

import copy
import json
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

from synthorg.integrations.connections.models import Connection
from synthorg.integrations.errors import SecretRetrievalError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import SECRET_RETRIEVAL_FAILED

if TYPE_CHECKING:
    from synthorg.persistence.secret_backends.protocol import SecretBackend

logger = get_logger(__name__)


class CredentialResolverMixin:
    """Credential decrypt/merge methods mixed into :class:`ConnectionCatalog`."""

    if TYPE_CHECKING:
        _secret_backend: SecretBackend

        async def get(self, name: str) -> Connection | None:
            """Load a connection or return ``None`` (provided by the host)."""
            ...

        async def get_or_raise(self, name: str) -> Connection:
            """Load a connection or raise (provided by the host class)."""
            ...

        def _name_lock(self, name: str) -> AbstractAsyncContextManager[None]:
            """Hold the connection's mutation lock (provided by the host)."""
            ...

    async def get_credentials(self, name: str) -> dict[str, str]:
        """Retrieve decrypted credentials for a connection.

        Resolves all ``SecretRef`` entries and returns the merged
        credential dict.

        Args:
            name: Connection name to resolve credentials for.

        Returns:
            A merged dict of plaintext credential key-value pairs from
            all of the connection's ``SecretRef`` entries.

        Raises:
            ConnectionNotFoundError: If the connection does not exist.
            SecretRetrievalError: If a referenced secret is missing
                or cannot be decoded.
        """
        # Held across the lookup and the decrypt: a concurrent delete()
        # removes the row and then its secrets, so an unlocked reader can
        # resolve a connection whose secrets are being deleted underneath
        # it and raise a retrieval fault for a connection that is simply
        # gone. Deadlock-free because no writer resolves credentials, and
        # ``get`` itself never takes this lock.
        async with self._name_lock(name):
            conn = await self.get_or_raise(name)
            return await self._resolve_credentials_for(conn)

    async def get_credentials_or_none(self, name: str) -> dict[str, str] | None:
        """Retrieve decrypted credentials, or ``None`` when unconfigured.

        The quiet counterpart to :meth:`get_credentials`, for callers whose
        "no such connection" is a routine state rather than a fault: a probe
        asking whether an optional integration has been set up yet runs on
        every dashboard poll, and routing it through the raising variant
        turns a normal answer into a stream of exceptions.

        Args:
            name: Connection name to resolve credentials for.

        Returns:
            The merged plaintext credential dict, or ``None`` when no
            connection with that name exists.

        Raises:
            SecretRetrievalError: If the connection exists but a referenced
                secret is missing or cannot be decoded. A broken secret is a
                fault even when absence is not.
        """
        async with self._name_lock(name):
            conn = await self.get(name)
            if conn is None:
                return None
            return await self._resolve_credentials_for(conn)

    async def _resolve_credentials_for(
        self,
        conn: Connection,
    ) -> dict[str, str]:
        """Decrypt and merge credentials for a pre-loaded ``Connection``.

        Extracted from :meth:`get_credentials` so callers that have
        already loaded the connection under a lock can reuse the
        merge logic without hitting the cache a second time.

        Args:
            conn: The already-loaded connection to resolve secrets for.

        Returns:
            A deep-copied dict of merged plaintext credential key-value
            pairs from all of the connection's ``SecretRef`` entries.

        Raises:
            SecretRetrievalError: If a referenced secret is missing or
                cannot be decoded by the backend.
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
