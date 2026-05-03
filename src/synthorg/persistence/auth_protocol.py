"""Auth repository protocols -- sessions, lockouts, refresh tokens.

Hybrid in-memory + durable contracts for the auth hot path.  Session
revocation and account lockout state is cached in-process for O(1)
synchronous checks from the request-handling fast path; the repository
interface exposes durable read/write operations plus the cache.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from synthorg.api.auth.refresh_record import (
        RefreshConsumeOutcome,
    )
    from synthorg.api.auth.session import Session


@runtime_checkable
class SessionRepository(Protocol):
    """Durable session store with an in-memory revocation cache.

    The ``is_revoked`` method is synchronous (O(1) set lookup) for use
    on the request hot path without blocking the event loop.  All other
    methods are async and hit the durable backend.

    Attributes:
        _revoked: In-memory cache of revoked session IDs.  Part of the
            protocol surface so test fixtures can clear it between tests
            without casting to a concrete implementation.
    """

    _revoked: set[str]

    async def load_revoked(self) -> None:
        """Load revoked session IDs from durable storage into memory."""
        ...

    async def create(self, session: Session) -> None:
        """Persist a new session."""
        ...

    async def get(self, session_id: str) -> Session | None:
        """Look up a session by ID, or return ``None`` if missing."""
        ...

    async def list_by_user(
        self,
        user_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List active sessions for a user, optionally paginated."""
        ...

    async def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List all active sessions, optionally paginated."""
        ...

    async def revoke(self, session_id: str) -> bool:
        """Revoke a session by ID; return ``True`` if it existed."""
        ...

    async def revoke_all_for_user(self, user_id: str) -> int:
        """Revoke every active session for a user; return the count."""
        ...

    async def enforce_session_limit(
        self,
        user_id: str,
        max_sessions: int,
    ) -> int:
        """Revoke oldest sessions when a user exceeds the concurrent limit."""
        ...

    def is_revoked(self, session_id: str) -> bool:
        """Synchronous, O(1) revocation check for the auth hot path."""
        ...

    async def cleanup_expired(self) -> int:
        """Remove expired sessions from durable storage; return count."""
        ...


@runtime_checkable
class LockoutRepository(Protocol):
    """Account lockout store with an in-memory lock-cache for the hot path.

    The ``is_locked`` method is synchronous (O(1) dict lookup) for use
    on the login attempt hot path.
    """

    async def load_locked(self) -> int:
        """Restore in-memory lockout state from durable storage."""
        ...

    async def record_failure(
        self,
        username: str,
        ip_address: str = "",
    ) -> bool:
        """Record a failed login attempt; return ``True`` if now locked."""
        ...

    async def record_success(self, username: str) -> bool:
        """Clear failure count on successful login.

        Returns ``True`` when a previously-locked account was
        unlocked (caller logs ``SECURITY_AUTH_LOCKOUT_CLEARED``);
        ``False`` when no lockout was in effect.
        """
        ...

    async def cleanup_expired(self) -> int:
        """Remove old attempt records; return count removed."""
        ...

    def is_locked(self, username: str) -> bool:
        """Synchronous, O(1) lockout check for the auth hot path."""
        ...

    @property
    def lockout_duration_seconds(self) -> int:
        """Return the lockout duration in seconds for Retry-After."""
        ...

    @property
    def threshold(self) -> int:
        """Failed-attempt threshold; used by the auth controller audit."""
        ...


@runtime_checkable
class RefreshTokenRepository(Protocol):
    """Refresh-token store with single-use rotation semantics."""

    async def create(
        self,
        token_hash: str,
        session_id: str,
        user_id: str,
        expires_at: datetime,
    ) -> None:
        """Store a new refresh token."""
        ...

    async def consume(
        self,
        token_hash: str,
        *,
        is_session_revoked: Callable[[str], bool] | None = None,
    ) -> RefreshConsumeOutcome:
        """Atomically consume a refresh token (single-use rotation).

        Returns a structured outcome that carries either the
        consumed :class:`RefreshRecord` (success) or a typed
        :class:`RefreshRejectReason` (``session_revoked`` /
        ``replay_detected`` / ``not_found_or_expired``) so the
        service layer can emit ``SECURITY_AUTH_REFRESH_REJECTED``
        with an accurate reason. The repo MUST NOT log the audit
        event itself per the persistence-boundary rule.
        """
        ...

    async def revoke_by_session(self, session_id: str) -> int:
        """Mark all refresh tokens for a session as used."""
        ...

    async def revoke_by_user(self, user_id: str) -> int:
        """Mark all refresh tokens for a user as used."""
        ...

    async def cleanup_expired(self) -> int:
        """Remove expired tokens."""
        ...
