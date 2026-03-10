"""Authentication service — password hashing, JWT ops, API key hashing."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import argon2
import jwt

from ai_company.api.auth.models import User  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.api import API_AUTH_TOKEN_ISSUED

if TYPE_CHECKING:
    from ai_company.api.auth.config import AuthConfig

logger = get_logger(__name__)

_hasher = argon2.PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


class AuthService:
    """Stateless authentication operations.

    Args:
        config: Authentication configuration (carries JWT secret).
    """

    def __init__(self, config: AuthConfig) -> None:
        self._config = config

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
        """
        try:
            return _hasher.verify(password_hash, password)
        except argon2.exceptions.VerifyMismatchError:
            return False
        except argon2.exceptions.VerificationError:
            return False

    def create_token(self, user: User) -> tuple[str, int]:
        """Create a JWT for the given user.

        Args:
            user: Authenticated user.

        Returns:
            Tuple of (encoded JWT string, expiry seconds).
        """
        now = datetime.now(UTC)
        expiry_seconds = self._config.jwt_expiry_minutes * 60
        payload: dict[str, Any] = {
            "sub": user.id,
            "username": user.username,
            "role": user.role.value,
            "must_change_password": user.must_change_password,
            "iat": now,
            "exp": now + timedelta(seconds=expiry_seconds),
        }
        token = jwt.encode(
            payload,
            self._config.jwt_secret,
            algorithm=self._config.jwt_algorithm,
        )
        logger.info(
            API_AUTH_TOKEN_ISSUED,
            user_id=user.id,
            username=user.username,
        )
        return token, expiry_seconds

    def decode_token(self, token: str) -> dict[str, Any]:
        """Decode and validate a JWT.

        Args:
            token: Encoded JWT string.

        Returns:
            Decoded claims dictionary.

        Raises:
            jwt.InvalidTokenError: If the token is invalid or expired.
        """
        return jwt.decode(
            token,
            self._config.jwt_secret,
            algorithms=[self._config.jwt_algorithm],
        )

    @staticmethod
    def hash_api_key(raw_key: str) -> str:
        """Compute SHA-256 hex digest of a raw API key.

        Args:
            raw_key: The plaintext API key.

        Returns:
            Lowercase hex digest.
        """
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @staticmethod
    def verify_api_key(raw_key: str, stored_hash: str) -> bool:
        """Constant-time comparison of API key hash.

        Args:
            raw_key: Plaintext API key from request.
            stored_hash: SHA-256 hex digest from storage.

        Returns:
            ``True`` if the key matches.
        """
        computed = hashlib.sha256(raw_key.encode()).hexdigest()
        return hmac.compare_digest(computed, stored_hash)

    @staticmethod
    def generate_api_key() -> str:
        """Generate a cryptographically secure API key.

        Returns:
            URL-safe base64 string (43 chars).
        """
        return secrets.token_urlsafe(32)
