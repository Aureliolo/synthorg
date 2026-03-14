"""Authentication service — password hashing, JWT ops, API key hashing."""

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import argon2
import jwt

from synthorg.api.auth.models import User  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_AUTH_FAILED

if TYPE_CHECKING:
    from synthorg.api.auth.config import AuthConfig

logger = get_logger(__name__)


class SecretNotConfiguredError(RuntimeError):
    """Raised when the JWT secret is required but not configured."""


_hasher = argon2.PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


class AuthService:
    """Immutable authentication operations.

    Args:
        config: Authentication configuration (carries JWT secret).
    """

    def __init__(self, config: AuthConfig) -> None:
        self._config = config

    def _require_secret(self, operation: str) -> str:
        """Return the JWT secret or raise if unconfigured.

        Args:
            operation: Name of the calling operation (for logging).

        Returns:
            The JWT secret string.

        Raises:
            SecretNotConfiguredError: If the JWT secret is empty.
        """
        secret = self._config.jwt_secret
        if not secret:
            msg = "JWT secret not configured"
            logger.error(
                API_AUTH_FAILED,
                reason="jwt_secret_missing",
                operation=operation,
            )
            raise SecretNotConfiguredError(msg)
        return secret

    def hash_password(self, password: str) -> str:
        """Hash a password with Argon2id.

        Args:
            password: Plaintext password.

        Returns:
            Argon2id hash string.
        """
        return _hasher.hash(password)

    def verify_password(self, password: str, password_hash: str) -> bool:
        """Verify a password against an Argon2id hash.

        Args:
            password: Plaintext password to check.
            password_hash: Stored Argon2id hash.

        Returns:
            ``True`` if the password matches.

        Raises:
            argon2.exceptions.VerificationError: On non-mismatch
                verification failures (e.g. unsupported parameters).
            argon2.exceptions.InvalidHashError: If the stored hash
                is corrupted or malformed (data integrity issue).
        """
        try:
            return _hasher.verify(password_hash, password)
        except argon2.exceptions.VerifyMismatchError:
            return False
        except argon2.exceptions.VerificationError:
            logger.warning(
                API_AUTH_FAILED,
                reason="hash_verification_error",
                exc_info=True,
            )
            raise
        except argon2.exceptions.InvalidHashError:
            logger.error(
                API_AUTH_FAILED,
                reason="invalid_hash_data_corruption",
                exc_info=True,
            )
            raise

    async def hash_password_async(self, password: str) -> str:
        """Hash a password with Argon2id in a thread executor.

        Offloads the CPU-intensive hashing to avoid blocking the
        event loop.

        Args:
            password: Plaintext password.

        Returns:
            Argon2id hash string.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.hash_password, password)

    async def verify_password_async(
        self,
        password: str,
        password_hash: str,
    ) -> bool:
        """Verify a password against an Argon2id hash in a thread executor.

        Offloads the CPU-intensive verification to avoid blocking the
        event loop.

        Args:
            password: Plaintext password to check.
            password_hash: Stored Argon2id hash.

        Returns:
            ``True`` if the password matches.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.verify_password, password, password_hash
        )

    def create_token(self, user: User) -> tuple[str, int]:
        """Create a JWT for the given user.

        The token includes a ``pwd_sig`` claim — a 16-character
        truncated SHA-256 of the stored password hash.  This is
        plain SHA-256, not HMAC — the password hash is already a
        high-entropy Argon2id output, and the claim is protected
        by the JWT signature.  The auth middleware validates this
        claim on every request so that tokens issued before a
        password change are automatically rejected.

        Args:
            user: Authenticated user.

        Returns:
            Tuple of (encoded JWT string, expiry seconds).

        Raises:
            SecretNotConfiguredError: If the JWT secret is empty.
        """
        secret = self._require_secret("create_token")
        now = datetime.now(UTC)
        expiry_seconds = self._config.jwt_expiry_minutes * 60
        pwd_sig = hashlib.sha256(
            user.password_hash.encode(),
        ).hexdigest()[:16]
        payload: dict[str, Any] = {
            "sub": user.id,
            "username": user.username,
            "role": user.role.value,
            "must_change_password": user.must_change_password,
            "pwd_sig": pwd_sig,
            "iat": now,
            "exp": now + timedelta(seconds=expiry_seconds),
        }
        token = jwt.encode(
            payload,
            secret,
            algorithm=self._config.jwt_algorithm,
        )
        return token, expiry_seconds

    def decode_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a JWT.

        Args:
            token: Encoded JWT string.

        Returns:
            Decoded claims dictionary.

        Raises:
            SecretNotConfiguredError: If the JWT secret is empty.
            jwt.InvalidTokenError: If the token is invalid or expired.
        """
        secret = self._require_secret("decode_token")
        return jwt.decode(
            token,
            secret,
            algorithms=[self._config.jwt_algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )

    def hash_api_key(self, raw_key: str) -> str:
        """Compute HMAC-SHA256 hex digest of a raw API key.

        Uses the server-side JWT secret as the HMAC key so that
        an attacker with read access to stored hashes cannot
        brute-force API keys offline.

        Args:
            raw_key: The plaintext API key.

        Returns:
            Lowercase hex digest.

        Raises:
            SecretNotConfiguredError: If the JWT secret is empty.
        """
        secret = self._require_secret("hash_api_key")
        return hmac.digest(
            secret.encode(),
            raw_key.encode(),
            "sha256",
        ).hex()

    @staticmethod
    def generate_api_key() -> str:
        """Generate a cryptographically secure API key.

        Returns:
            URL-safe base64 string (43 chars).
        """
        return secrets.token_urlsafe(32)
