"""Authentication service -- password hashing, JWT ops, API key hashing."""

import asyncio
import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final

import argon2
import jwt
from pydantic import BaseModel, ConfigDict

from synthorg.api.auth.claims import JwtClaims
from synthorg.api.auth.system_user import USER_AUDIENCE, USER_ISSUER
from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.models import User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.auth.token_size import get_auth_token_bytes
from synthorg.core.boundary import parse_typed
from synthorg.core.domain_errors import (
    RefreshTokenInvalidError,
    ServiceUnavailableError,
)
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.security import (
    SECURITY_AUTH_FAILED,
    SECURITY_AUTH_REFRESH_CREATED,
    SECURITY_AUTH_REFRESH_REJECTED,
)
from synthorg.persistence.auth_protocol import RefreshTokenRepository
from synthorg.persistence.user_protocol import UserRepository

logger = get_logger(__name__)


class SecretNotConfiguredError(ServiceUnavailableError):
    """Raised when the JWT secret is required but not configured."""

    default_message: ClassVar[str] = "JWT secret not configured"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    status_code: ClassVar[int] = 503


# Argon2id parameters. Named so a future OWASP / RFC 9106 ratchet of the
# profile (memory_cost in particular) is a one-line constant change, not a
# grep hunt. These are algorithm parameters, not operator-tunable settings.
_ARGON2_TIME_COST: Final[int] = 3
_ARGON2_MEMORY_COST: Final[int] = 65536
_ARGON2_PARALLELISM: Final[int] = 4
_ARGON2_HASH_LEN: Final[int] = 32
_ARGON2_SALT_LEN: Final[int] = 16

_hasher = argon2.PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_COST,
    parallelism=_ARGON2_PARALLELISM,
    hash_len=_ARGON2_HASH_LEN,
    salt_len=_ARGON2_SALT_LEN,
)


class RefreshRotation(BaseModel):
    """Result of a successful refresh-token rotation.

    The controller turns this into the session/csrf/refresh cookies
    and emits the post-persistence ``SECURITY_AUTH_REFRESH_CONSUMED``
    audit event. ``session_id`` is the *original* session id (the
    access token rotated in place), not a freshly minted one.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    token: NotBlankStr
    expires_in: int
    session_id: NotBlankStr
    user: User


class AuthService:
    """Immutable authentication operations.

    Owns the cryptographic primitives behind login: Argon2id password
    hashing and verification, JWT mint and decode, HMAC-SHA256 API
    key hashing, secure API key generation, and refresh-token
    persistence through the auth-domain boundary.

    Args:
        config: Authentication configuration (carries JWT secret).

    **Async vs sync.** Methods follow a single rule: an operation is
    declared ``async`` only when it touches an event-loop boundary --
    either offloading CPU-bound work via :func:`asyncio.to_thread`,
    or awaiting a repository write. Everything else stays sync.

    - :meth:`hash_password` and :meth:`verify_password`
      are async because Argon2id is CPU-bound (3 time-cost iterations
      over 64MiB of memory by default); :func:`asyncio.to_thread`
      keeps a single login from stalling every concurrent request
      waiting on the loop.
    - :meth:`persist_refresh_token` is async because it awaits a
      repository write through the auth-domain boundary.
    - :meth:`create_token`, :meth:`decode_token`, :meth:`hash_api_key`,
      and :meth:`generate_api_key` are sync: each is either pure CPU
      with bounded sub-millisecond cost (HMAC, ``secrets.token_urlsafe``)
      or an in-process JWT codec call with no I/O.

    **Thread-safety.** Instances are safe to share across the
    request-handler pool without external locking. After
    :meth:`__init__`, the only state held is ``_config: AuthConfig``
    -- itself a Pydantic ``frozen=True`` model. The module-global
    :class:`argon2.PasswordHasher` is configured once at import and
    treated as a deployment-wide concern (Argon2 parameter selection
    is not per-request); the underlying ``argon2`` and ``jwt``
    libraries are stateless and thread-safe.

    **Out of scope.** This service does not implement token
    revocation (the auth middleware enforces that by checking
    ``pwd_sig`` on every request), session storage (handled by the
    refresh-token repository), or SYSTEM-role token minting
    (rejected by :meth:`create_token`; SYSTEM tokens are minted by
    the Go CLI with :data:`SYSTEM_ISSUER` / :data:`SYSTEM_AUDIENCE`).
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

    async def hash_password(self, password: str) -> str:
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

    async def verify_password(
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
            log_exception_redacted(
                logger, SECURITY_AUTH_FAILED, exc, reason="invalid_hash_data_corruption"
            )
            raise

    def create_token(
        self,
        user: User,
        *,
        session_id: str | None = None,
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
            session_id: Reuse this session id (``jti``) instead of
                minting a fresh one. Refresh-token rotation passes the
                consumed record's session id so the access token
                rotates *within* the existing session rather than
                spawning a new one (which would orphan the old
                session and saturate ``max_concurrent_sessions``).

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
        session_id = session_id if session_id is not None else uuid.uuid4().hex
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
        :func:`synthorg.core.boundary.parse_typed` so a malformed claim
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
        store: RefreshTokenRepository,
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
                write through.
            token_hash: HMAC-SHA256 hex digest of the raw refresh token.
            session_id: Session identifier.
            user_id: User identifier.
            expires_at: Refresh token expiry (UTC).

        Raises:
            QueryError: If the underlying repo write fails.
        """
        await store.create(
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

    async def rotate_refresh_token(
        self,
        *,
        raw_refresh_token: str,
        refresh_store: RefreshTokenRepository,
        users: UserRepository,
        is_session_revoked: Callable[[str], bool] | None,
    ) -> RefreshRotation:
        """Single-use refresh rotation: consume, validate, re-mint.

        The reject matrix lives here (not the controller) so it is
        unit-testable without the full app: a missing / replayed /
        expired refresh token or a revoked session emits
        ``SECURITY_AUTH_REFRESH_REJECTED`` (typed reason) and raises
        :class:`RefreshTokenInvalidError` (HTTP 401, code 1005). The
        success path re-mints the access token *within the consumed
        record's session* so rotation does not orphan the session or
        saturate ``max_concurrent_sessions``.

        ``SECURITY_AUTH_REFRESH_CONSUMED`` is emitted by the caller
        AFTER the rotated refresh row is persisted (state-transition
        events log after the write), so it is intentionally not
        emitted here.

        Args:
            raw_refresh_token: The opaque refresh cookie value.
            refresh_store: Repository providing single-use
                ``consume`` (CAS + replay + session-revocation).
            users: User repository for the post-consume owner lookup.
            is_session_revoked: Predicate passed into ``consume`` so
                a revoked session rejects rotation.

        Returns:
            A :class:`RefreshRotation` with the new access token and
            the preserved session id.

        Raises:
            RefreshTokenInvalidError: For any reject path (missing
                cookie, consume rejection, or owner deleted between
                issuance and rotation).
        """
        if not raw_refresh_token:
            logger.warning(SECURITY_AUTH_REFRESH_REJECTED, reason="cookie_missing")
            raise RefreshTokenInvalidError

        token_hash = self.hash_api_key(raw_refresh_token)
        outcome = await refresh_store.consume(
            token_hash,
            is_session_revoked=is_session_revoked,
        )
        if outcome.reject_reason is not None:
            logger.warning(
                SECURITY_AUTH_REFRESH_REJECTED,
                reason=outcome.reject_reason.value,
            )
            raise RefreshTokenInvalidError

        record = outcome.record
        if record is None:
            # RefreshConsumeOutcome's validator guarantees exactly one
            # of record / reject_reason is set and reject_reason was
            # None above, so this is unreachable in practice. Handle it
            # explicitly anyway (not `assert`, which `python -O`
            # strips) so the security path fails closed if the
            # invariant is ever violated by a future change.
            logger.warning(
                SECURITY_AUTH_REFRESH_REJECTED,
                reason="consume_outcome_invariant_violation",
            )
            raise RefreshTokenInvalidError

        user = await users.get(record.user_id)
        if user is None:
            # The token row is already marked used; it cannot be
            # un-consumed. Reject so a deleted owner cannot rotate.
            logger.warning(
                SECURITY_AUTH_REFRESH_REJECTED,
                reason="user_not_found_after_consume",
                user_id=record.user_id,
            )
            raise RefreshTokenInvalidError

        token, expires_in, session_id = self.create_token(
            user,
            session_id=record.session_id,
        )
        return RefreshRotation(
            token=token,
            expires_in=expires_in,
            session_id=session_id,
            user=user,
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
