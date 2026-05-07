"""Authentication service -- password hashing, JWT ops, API key hashing."""

import asyncio
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar

import argon2
import jwt

from synthorg.api.auth.claims import JwtClaims
from synthorg.api.auth.system_user import USER_AUDIENCE, USER_ISSUER
from synthorg.api.auth.token_size import get_auth_token_bytes
from synthorg.api.boundary import parse_typed
from synthorg.core.auth.models import User  # noqa: TC001
from synthorg.core.auth.roles import HumanRole
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import (
    SECURITY_AUTH_FAILED,
    SECURITY_AUTH_REFRESH_CREATED,
)

if TYPE_CHECKING:
    from synthorg.core.auth.config import AuthConfig

logger = get_logger(__name__)


class SecretNotConfiguredError(ServiceUnavailableError):
    """Raised when the JWT secret is required but not configured."""

    default_message: ClassVar[str] = "JWT secret not configured"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    status_code: ClassVar[int] = 503


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
                SECURITY_AUTH_FAILED,
                reason="jwt_secret_missing",
                operation=operation,
            )
            raise SecretNotConfiguredError(msg)
        return secret

    async def hash_password_async(self, password: str) -> str:
        """Hash a password with Argon2id off the event loop.

        Argon2id is CPU-bound; ``asyncio.to_thread`` defers the work
        to the default thread pool so a single login request cannot
        stall every concurrent request waiting on the loop.

        Args:
            password: Plaintext password.

        Returns:
            Argon2id hash string.
        """
        return await asyncio.to_thread(_hasher.hash, password)

    async def verify_password_async(
        self,
        password: str,
        password_hash: str,
    ) -> bool:
        """Verify a password against an Argon2id hash off the event loop.

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
            return await asyncio.to_thread(_hasher.verify, password_hash, password)
        except argon2.exceptions.VerifyMismatchError:
            return False
        except argon2.exceptions.VerificationError as exc:
            logger.warning(
                SECURITY_AUTH_FAILED,
                reason="hash_verification_error",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        except argon2.exceptions.InvalidHashError as exc:
            logger.error(
                SECURITY_AUTH_FAILED,
                reason="invalid_hash_data_corruption",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    def create_token(
        self,
        user: User,
    ) -> tuple[str, int, str]:
        """Create a JWT for the given **human** user.

        The token includes a ``pwd_sig`` claim -- a 16-character
        truncated SHA-256 of the stored password hash.  This is
        plain SHA-256, not HMAC -- the password hash is already a
        high-entropy Argon2id output, and the claim is protected
        by the JWT signature.  The auth middleware validates this
        claim on every request so that tokens issued before a
        password change are automatically rejected.

        A ``jti`` (JWT ID) claim is included for per-token session
        tracking and revocation.

        SYSTEM-role tokens are minted by the Go CLI with
        :data:`SYSTEM_ISSUER` / :data:`SYSTEM_AUDIENCE` -- never by
        this method. Calling ``create_token`` with a SYSTEM user
        would mint a token bearing :data:`USER_ISSUER` /
        :data:`USER_AUDIENCE`, which the middleware's
        ``_resolve_jwt_user`` immediately rejects (per-role iss/aud
        enforcement). We fail-fast with ``ValueError`` here so a
        future caller that accidentally passes a SYSTEM user
        surfaces the problem at mint time, not at the next request.

        The claim shape is built through :class:`JwtClaims` so the
        encode-side payload is statically typed and the decode-side
        boundary helper validates against the same model.

        Args:
            user: Authenticated human user.

        Returns:
            Tuple of (encoded JWT, expiry seconds, session ID).

        Raises:
            SecretNotConfiguredError: If the JWT secret is empty.
            ValueError: If *user* has the SYSTEM role -- mint via
                the CLI's system-token path instead.
        """
        if user.role is HumanRole.SYSTEM:
            msg = (
                "create_token cannot mint SYSTEM-role tokens; "
                "system tokens are issued by the CLI with "
                "SYSTEM_ISSUER / SYSTEM_AUDIENCE"
            )
            raise ValueError(msg)
        secret = self._require_secret("create_token")
        now = datetime.now(UTC)
        expiry_seconds = self._config.jwt_expiry_minutes * 60
        session_id = uuid.uuid4().hex
        pwd_sig = hashlib.sha256(
            user.password_hash.encode(),
        ).hexdigest()[:16]
        claims = JwtClaims(
            iss=USER_ISSUER,
            aud=USER_AUDIENCE,
            sub=user.id,
            jti=session_id,
            iat=int(now.timestamp()),
            exp=int((now + timedelta(seconds=expiry_seconds)).timestamp()),
            username=user.username,
            role=user.role,
            must_change_password=user.must_change_password,
            pwd_sig=pwd_sig,
        )
        token = jwt.encode(
            claims.model_dump(mode="json"),
            secret,
            algorithm=self._config.jwt_algorithm,
        )
        return token, expiry_seconds, session_id

    def decode_token(self, token: str) -> JwtClaims:
        """Decode and validate a JWT into a typed claim set.

        Issuer (``iss``) and audience (``aud``) verification is
        intentionally deferred to the auth middleware's
        ``_resolve_jwt_user``: the canonical pair differs by role
        (``synthorg-cli`` / ``synthorg-backend`` for CLI-minted
        SYSTEM tokens vs. ``synthorg-api`` / ``synthorg-api`` for
        API-minted user tokens), and the middleware loads the user
        record before deciding which pair to enforce. Both claims are
        ``require``-listed here so a missing claim fails decode rather
        than reaching the middleware as ``None``.

        After PyJWT validates the signature and required claims, the
        raw payload is routed through
        :func:`synthorg.api.boundary.parse_typed` so a malformed claim
        set (extra keys, type mismatch, ``iat >= exp``) is rejected at
        the boundary with a structured ``api.boundary.validation_failed``
        log instead of slipping through and surprising a downstream
        attribute access.

        Args:
            token: Encoded JWT string.

        Returns:
            Validated :class:`JwtClaims` instance.

        Raises:
            SecretNotConfiguredError: If the JWT secret is empty.
            jwt.InvalidTokenError: If the token signature, expiry,
                or required claim set is invalid.
            ValidationError: If the decoded claim set does not
                conform to :class:`JwtClaims` (extra keys, wrong
                types, or violated invariants).
        """
        secret = self._require_secret("decode_token")
        raw_claims = jwt.decode(
            token,
            secret,
            algorithms=[self._config.jwt_algorithm],
            options={
                "require": ["exp", "iat", "sub", "jti", "iss", "aud"],
                "verify_aud": False,
                "verify_iss": False,
            },
        )
        return parse_typed("jwt", raw_claims, JwtClaims)

    async def persist_refresh_token(
        self,
        store: object,
        *,
        token_hash: str,
        session_id: str,
        user_id: str,
        expires_at: datetime,
    ) -> None:
        """Persist a refresh token through the auth-domain boundary.

        Centralises the refresh-store write + audit log so callers
        (notably ``make_session_cookies``) do not reach into
        ``app_state._refresh_store`` directly.  The repo handle is
        passed in rather than held by the service so this stays
        compatible with the existing AuthService construction (no
        constructor change required).

        Args:
            store: The :class:`RefreshTokenRepository` instance to
                write through.  Typed as ``object`` to keep this
                module free of persistence-layer imports.
            token_hash: HMAC-SHA256 hex digest of the raw refresh token.
            session_id: Session identifier.
            user_id: User identifier.
            expires_at: Refresh token expiry (UTC).

        Raises:
            QueryError: If the underlying repo write fails.
        """
        await store.create(  # type: ignore[attr-defined]
            token_hash=token_hash,
            session_id=session_id,
            user_id=user_id,
            expires_at=expires_at,
        )
        logger.info(
            SECURITY_AUTH_REFRESH_CREATED,
            session_id=session_id,
            user_id=user_id,
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
            URL-safe base64 string sized by ``security.auth_token_bytes``
            (default 32 bytes / 43 base64 chars).
        """
        return secrets.token_urlsafe(get_auth_token_bytes())
