"""Environment variable secret backend (read-only).

Secrets are read from environment variables with a configurable
prefix.  This backend does not support store, delete, or rotate;
it is intended for development and simple deployments where secrets
are managed externally (e.g. Docker secrets, systemd credentials).
"""

import os

from synthorg.core.types import NotBlankStr
from synthorg.integrations.config import EnvVarConfig
from synthorg.integrations.errors import SecretStorageError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    SECRET_BACKEND_UNAVAILABLE,
    SECRET_RETRIEVAL_FAILED,
    SECRET_RETRIEVED,
)

logger = get_logger(__name__)


class EnvVarSecretBackend:
    """Read-only secret backend backed by environment variables.

    Secret IDs are mapped to env vars as ``{prefix}{secret_id}``.

    Args:
        config: Env var backend configuration.
    """

    def __init__(
        self,
        config: EnvVarConfig | None = None,
    ) -> None:
        self._prefix = (config or EnvVarConfig()).prefix

    @property
    def backend_name(self) -> NotBlankStr:
        """Human-readable backend identifier.

        Returns:
            Result of type ``NotBlankStr``.
        """
        return "env_var"

    async def store(
        self,
        secret_id: NotBlankStr,
        value: bytes,  # noqa: ARG002
    ) -> None:
        """Not supported -- environment is read-only.

        Raises:
            SecretStorageError: If the secret store rejects the write.
        """
        logger.warning(
            SECRET_BACKEND_UNAVAILABLE,
            backend=self.backend_name,
            operation="store",
            secret_id=secret_id,
        )
        msg = "EnvVarSecretBackend is read-only; cannot store secrets"
        raise SecretStorageError(msg)

    async def retrieve(self, secret_id: NotBlankStr) -> bytes | None:
        """Read a secret from the environment.

        Returns:
            The matching value, or ``None`` when absent.
        """
        env_key = f"{self._prefix}{secret_id}"
        raw = os.environ.get(env_key)
        if raw is None:
            logger.debug(
                SECRET_RETRIEVAL_FAILED,
                secret_id=secret_id,
                error="env var not set",
            )
            return None
        logger.debug(SECRET_RETRIEVED, secret_id=secret_id)
        return raw.encode("utf-8")

    async def delete(
        self,
        secret_id: NotBlankStr,
    ) -> bool:
        """Not supported -- environment is read-only.

        Raises:
            SecretStorageError: If the secret store rejects the write.
        """
        logger.warning(
            SECRET_BACKEND_UNAVAILABLE,
            backend=self.backend_name,
            operation="delete",
            secret_id=secret_id,
        )
        msg = "EnvVarSecretBackend is read-only; cannot delete secrets"
        raise SecretStorageError(msg)

    async def rotate(
        self,
        old_id: NotBlankStr,
        new_value: bytes,  # noqa: ARG002
    ) -> NotBlankStr:
        """Not supported -- environment is read-only.

        Raises:
            SecretStorageError: If the secret store rejects the write.
        """
        logger.warning(
            SECRET_BACKEND_UNAVAILABLE,
            backend=self.backend_name,
            operation="rotate",
            old_id=old_id,
        )
        msg = "EnvVarSecretBackend is read-only; cannot rotate secrets"
        raise SecretStorageError(msg)

    async def close(self) -> None:
        """No-op."""
