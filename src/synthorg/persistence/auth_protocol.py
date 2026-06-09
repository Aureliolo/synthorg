"""Auth repository protocols -- sessions, lockouts, refresh tokens.

Hybrid in-memory + durable contracts for the auth hot path.  Session
revocation and account lockout state is cached in-process for O(1)
synchronous checks from the request-handling fast path; the repository
interface exposes durable read/write operations plus the cache.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, override, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.auth.refresh_record import (
    RefreshConsumeOutcome,
)
from synthorg.core.auth.session import Session
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import (
    DEFAULT_PAGE_SIZE,
    FilteredQueryRepository,
    IdKeyedRepository,
)
from synthorg.persistence._shared.pagination import DEFAULT_LIST_LIMIT

__all__ = [
    "LockoutRepository",
    "RefreshTokenRepository",
    "SessionFilterSpec",
    "SessionRepository",
]


class SessionFilterSpec(BaseModel):
    """Filter spec for ``SessionRepository.query``."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    user_id: NotBlankStr | None = Field(
        default=None,
        description="Filter by session owner user ID",
    )
    revoked: bool | None = Field(
        default=None,
        description="Filter by revocation status",
    )


@runtime_checkable
class SessionRepository(
    IdKeyedRepository["Session", NotBlankStr],
    FilteredQueryRepository["Session", SessionFilterSpec],
    Protocol,
):
    """Durable session store with an in-memory revocation cache.

    Composes :class:`IdKeyedRepository` + :class:`FilteredQueryRepository`.
    ``is_revoked`` is a synchronous O(1) revocation check on the auth
    hot path; ``load_revoked`` preloads the in-memory cache at startup.

    Attributes:
        _revoked: In-memory cache of revoked session IDs.  Part of the
            protocol surface so test fixtures can clear it between tests
            without casting to a concrete implementation.
    """

    _revoked: set[str]

    async def load_revoked(self) -> None:
        """Load revoked session IDs from durable storage into memory."""
        ...

    @override
    async def save(self, entity: Session, /) -> None:
        """Persist a session (insert or update by session_id).

        Args:
            entity: The session to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def get(self, entity_id: NotBlankStr, /) -> Session | None:
        """Retrieve a session by session_id.

        Args:
            entity_id: The session identifier.

        Returns:
            The session, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List every persisted session, paginated.

        Canonical universe for this repository: **all** sessions,
        active and revoked alike. No active-only filtering is applied
        here -- callers that want only active sessions filter via
        :meth:`query` with ``revoked=False``.

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Sessions ordered by session_id ascending (the stable
            generic ``IdKeyedRepository`` ordering).

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def query(
        self,
        filter_spec: SessionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List sessions matching the filter spec.

        Args:
            filter_spec: Carries optional filters for user_id, revoked.
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Matching sessions ordered by session_id ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def count(self, filter_spec: SessionFilterSpec) -> int:
        """Count sessions matching the filter spec.

        Args:
            filter_spec: Carries optional filters.

        Returns:
            Total number of matching sessions.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    @override
    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete a session by session_id.

        Args:
            entity_id: The session identifier.

        Returns:
            ``True`` if the session was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_all(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List sessions newest-first (audit/history ordering).

        Same universe as :meth:`list_items` (all sessions, active and
        revoked) -- this is purely an alternate ordering
        (``created_at`` DESC instead of ``session_id`` ASC) for audit
        dashboards that need most-recent-first history. It is not an
        active-only vs all-sessions distinction; both methods return
        the full set.
        """
        ...

    async def list_by_user(
        self,
        user_id: NotBlankStr,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[Session, ...]:
        """List sessions for a user, newest-first (alternate-key view)."""
        ...

    async def revoke(self, session_id: NotBlankStr) -> bool:
        """Mark a session as revoked. Returns True iff it existed.

        Distinct from a generic save because the state transition also
        updates the ``load_revoked``-backed in-memory set, keeping the
        synchronous ``is_revoked`` hot-path check coherent.
        """
        ...

    async def revoke_all_for_user(self, user_id: NotBlankStr) -> int:
        """Revoke every active session for a user; return the count.

        Enforces the session-revocation policy as a single domain
        operation rather than a caller-orchestrated loop over saves.
        """
        ...

    async def enforce_session_limit(
        self,
        user_id: NotBlankStr,
        max_sessions: int,
    ) -> int:
        """Revoke oldest sessions when a user exceeds the concurrent limit.

        Enforces the per-user session-concurrency invariant atomically;
        the eviction choice (oldest-first) is domain policy the generic
        save surface cannot express.
        """
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

    Specialised hot-path failure-tracking cache with custom semantics:
    ``record_failure``/``record_success`` track attempts within a sliding
    window and enforce a threshold-based lockout, while ``is_locked`` is
    synchronous O(1) for the auth middleware. No CRUD operations apply
    because failure records are implicit (not persistent entities) and
    lockout state is computed on-the-fly from the attempt window.
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
    """Refresh-token store with single-use rotation semantics.

    Refresh tokens are stored by hash (a primary key), but the
    semantics are not standard CRUD: ``consume()`` is atomic
    compare-and-set (mark-as-used only if not-yet-used, with
    session-revocation check), and bulk revocation happens via
    ``revoke_by_session``/``revoke_by_user`` with no individual delete.
    The single-use contract and replay detection are domain invariants
    that require custom operations.
    """

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
