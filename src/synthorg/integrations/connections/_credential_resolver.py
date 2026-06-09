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

        async def get_or_raise(self, name: str) -> Connection:
            """Load a connection or raise (provided by the host class)."""
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
        conn = await self.get_or_raise(name)
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
