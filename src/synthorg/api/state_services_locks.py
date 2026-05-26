"""Request-lock registry and WS/auth-store accessors for ``AppState``.

Extracted from ``state_services.py`` to keep each module under the
project's 800-line ceiling.  Hosts the per-request lifecycle-lock
registry (with its bounded-size eviction sweep), the WebSocket /
revalidation timeout accessors, and the session / lockout / refresh /
auth-service accessors.  The mixin is combined into
:class:`~synthorg.api.state_services.AppStateServicesMixin` via
inheritance so the shared helpers (``_require_service``, ``_set_once``)
resolve at runtime.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from synthorg.api.auth.presence import UserPresence  # noqa: TC001
from synthorg.api.auth.service import AuthService  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_BRIDGE_CONFIG_REJECTED,
    REQUEST_LOCK_RELEASE_SKIPPED_WHILE_HELD,
)
from synthorg.persistence.auth_protocol import (
    LockoutRepository as LockoutStore,  # noqa: TC001
)
from synthorg.persistence.auth_protocol import (
    RefreshTokenRepository as RefreshStore,  # noqa: TC001
)
from synthorg.persistence.auth_protocol import (
    SessionRepository as SessionStore,  # noqa: TC001
)

if TYPE_CHECKING:
    import threading
    from collections import OrderedDict
    from collections.abc import AsyncIterator

logger = get_logger(__name__)

# Defence-in-depth cap on the per-AppState request-lock registry.
# ``scope_request`` retains the lock across handler exit (the next
# approve/reject for the same id needs it), so an authenticated
# client that scopes unique ids and never advances them would
# otherwise grow the dict forever. 10k is well above any realistic
# in-flight working set for a single org.
_MAX_REQUEST_LOCKS: int = 10_000

# Validation bounds for the operator-tunable WS / auth-revalidation
# knobs. These mirror the ``Field(ge=..., le=...)`` bounds on the
# corresponding bridge-config models; centralised here so the check,
# the structured-warning fields, and the error message can't drift.
_WS_FRAME_TIMEOUT_MIN_SECONDS: int = 1
_WS_FRAME_TIMEOUT_MAX_SECONDS: int = 600
_AUTH_REVALIDATE_WINDOW_MIN_SECONDS: int = 1
_AUTH_REVALIDATE_WINDOW_MAX_SECONDS: int = 3_600
_AUTH_REVALIDATE_MAX_FAILURES_MIN: int = 1
_AUTH_REVALIDATE_MAX_FAILURES_MAX: int = 100


def _reject_non_int(value: object, *, field: str) -> None:
    """Raise ``TypeError`` (with a structured warning) for non-int settings.

    The WS DoS-prevention setters expect ``int`` values resolved from
    ``ConfigResolver.get_int``; non-int values would otherwise raise
    ``TypeError`` at the bounds comparison without a structured log,
    leaving operators without a clear signal which knob was bad.

    Raises:
        TypeError: Raised on the corresponding failure path.
    """
    # ``isinstance(value, int)`` accepts ``bool`` (since ``bool`` is a
    # subclass of ``int`` in Python); explicitly reject it so flags
    # don't slip through as 0/1.
    if isinstance(value, bool) or not isinstance(value, int):
        logger.warning(
            API_BRIDGE_CONFIG_REJECTED,
            field=field,
            reason="invalid_type",
            provided_type=type(value).__name__,
        )
        msg = f"{field} must be int, got {type(value).__name__}"
        raise TypeError(msg)


class _RequestLockAuthMixin:
    """Mixin hosting the request-lock registry and WS/auth accessors.

    Must be combined with the rest of ``AppStateServicesMixin`` via
    multiple inheritance so the shared helper methods
    (``_require_service``, ``_set_once``) resolve at runtime.
    """

    _set_once: Any

    def _require_service[T](  # pragma: no cover
        self, service: T | None, name: str
    ) -> T:
        """Return *service* or raise (implemented on concrete ``AppState``)."""
        raise NotImplementedError

    # Slot attrs the mixin reads directly (populated on concrete class).
    _request_locks: OrderedDict[str, asyncio.Lock]
    _request_locks_guard: threading.Lock
    _request_lock_refs: dict[str, int]
    _ws_auth_timeout_seconds: float
    _ws_frame_timeout_seconds: int
    _auth_revalidate_window_seconds: int
    _auth_revalidate_max_failures: int
    _session_store: SessionStore | None
    _lockout_store: LockoutStore | None
    _refresh_store: RefreshStore | None
    _user_presence: UserPresence
    _auth_service: AuthService | None

    def get_or_create_request_lock(self, request_id: str) -> asyncio.Lock:
        """Return the per-request lifecycle lock, creating it if absent.

        Low-level primitive that exposes the cached Lock for tests and
        diagnostics. Production callers MUST go through
        :meth:`acquire_request_lock` instead, which pairs this with a
        refcount bump so a concurrent eviction sweep cannot drop the
        entry between receiving the Lock and entering ``async with``.

        The dict is guarded by a plain ``threading.Lock`` because
        ``asyncio.Lock`` instances can only be constructed inside a
        running event loop, so the registry needs a thread-safe
        "check, then create" that does not require an active loop to
        serialise itself.

        On insert, the registry is capped at ``_MAX_REQUEST_LOCKS``: if
        adding the new entry would exceed the cap, the oldest **idle**
        entries are evicted (still-held or in-flight locks are kept so
        an in-flight approve/reject never strands a waiter on an
        evicted Lock). The cap defends against an authenticated client
        that scopes unique ids and never advances them to a terminal
        state, which would otherwise grow the dict without bound.

        Returns:
            ``asyncio.Lock`` instance.
        """
        lock = self._request_locks.get(request_id)
        if lock is not None:
            return lock
        with self._request_locks_guard:
            lock = self._request_locks.get(request_id)
            if lock is None:
                lock = asyncio.Lock()
                self._request_locks[request_id] = lock
                if len(self._request_locks) > _MAX_REQUEST_LOCKS:
                    self._evict_idle_request_locks_locked(_MAX_REQUEST_LOCKS)
            return lock

    @asynccontextmanager
    async def acquire_request_lock(self, request_id: str) -> AsyncIterator[None]:
        """Acquire the per-request lifecycle lock with refcount tracking.

        Canonical entry point for serialising
        ``scope``/``approve``/``reject`` transitions on a request id.
        Bumps an in-flight refcount before returning the Lock so a
        concurrent eviction sweep (triggered when the registry hits
        ``_MAX_REQUEST_LOCKS``) cannot drop the entry between this
        method receiving the Lock and the body's implicit
        ``await lock.acquire()``. Without that gate, the next caller
        for the same id would mint a fresh Lock and two callers would
        end up holding *different* Lock objects for the same request,
        breaking the per-id ordering invariant.

        Mirrors the pattern in
        :mod:`synthorg.api.rate_limits.in_memory` (``_lock_refs``).
        """
        lock = self._reserve_request_lock(request_id)
        try:
            async with lock:
                yield
        finally:
            self._release_request_lock_ref(request_id)

    def _reserve_request_lock(self, request_id: str) -> asyncio.Lock:
        """Get-or-create the Lock and increment the in-flight refcount.

        Pairs with :meth:`_release_request_lock_ref`. Both operations
        execute under ``self._request_locks_guard`` so a concurrent
        eviction sweep observes the refcount bump and skips the entry.

        Returns:
            ``asyncio.Lock`` instance.
        """
        with self._request_locks_guard:
            lock = self._request_locks.get(request_id)
            if lock is None:
                lock = asyncio.Lock()
                self._request_locks[request_id] = lock
                if len(self._request_locks) > _MAX_REQUEST_LOCKS:
                    self._evict_idle_request_locks_locked(_MAX_REQUEST_LOCKS)
            self._request_lock_refs[request_id] = (
                self._request_lock_refs.get(request_id, 0) + 1
            )
            return lock

    def _release_request_lock_ref(self, request_id: str) -> None:
        """Drop one in-flight reference to the per-request Lock.

        The refs entry is removed (rather than left at 0) once the
        count drops to zero so a quiescent id contributes nothing to
        memory.
        """
        with self._request_locks_guard:
            count = self._request_lock_refs.get(request_id, 0) - 1
            if count <= 0:
                self._request_lock_refs.pop(request_id, None)
            else:
                self._request_lock_refs[request_id] = count

    def _evict_idle_request_locks_locked(self, target_size: int) -> None:
        """Evict oldest idle entries down to ``target_size``.

        Caller must already hold ``self._request_locks_guard``. Iterates
        the OrderedDict in insertion order; entries whose Lock is held
        OR whose in-flight refcount is non-zero are kept, so a
        long-running scope still in flight (or one whose caller has
        just received the Lock but not yet entered ``async with``) is
        never stranded on an evicted Lock object.
        """
        # Snapshot keys before mutating the OrderedDict during iteration.
        for request_id in list(self._request_locks.keys()):
            if len(self._request_locks) <= target_size:
                return
            lock = self._request_locks[request_id]
            if not lock.locked() and self._request_lock_refs.get(request_id, 0) == 0:
                self._request_locks.pop(request_id, None)

    def release_request_lock_if_idle(self, request_id: str) -> None:
        """Drop the lock for ``request_id`` after a terminal transition.

        Called after the final ``save`` of a terminal state (approve,
        reject) so the registry does not accumulate one entry per
        lifetime request id. Only evicts when the lock is idle and
        no in-flight refcount remains -- a still-held or in-flight
        entry would strand a waiter who already holds a reference to
        the same :class:`asyncio.Lock` object. The caller must already
        have left the ``async with acquire_request_lock`` block (or
        directly released the Lock returned by
        :meth:`get_or_create_request_lock`) before invoking this
        helper, otherwise the ``locked()`` probe or refcount check
        reports the caller's own hold and the eviction is a no-op.
        """
        with self._request_locks_guard:
            lock = self._request_locks.get(request_id)
            if lock is None:
                return
            if lock.locked() or self._request_lock_refs.get(request_id, 0) > 0:
                # Caller violated the documented contract -- they're
                # still holding the lock when asking us to evict it.
                # Surface as DEBUG so the next reader of the logs can
                # find the caller bug; not WARN because the no-op is
                # safe (the registry just keeps the entry).
                logger.debug(
                    REQUEST_LOCK_RELEASE_SKIPPED_WHILE_HELD,
                    request_id=request_id,
                )
                return
            self._request_locks.pop(request_id, None)

    @property
    def ws_auth_timeout_seconds(self) -> float:
        """Return the WebSocket first-message auth-handshake timeout.

        Populated by ``_apply_bridge_config`` from
        ``api.ws_auth_timeout_seconds`` (``restart_required=True``, so the
        operator-visible contract is "takes effect at the next restart");
        always has a sane built-in default (10.0 s) so the handler
        never reaches back through the resolver per-connection.  The
        setter below is permissive by design -- tests and subsystems that
        need a different value at runtime may call it -- so the effective
        value is whichever ``set_ws_auth_timeout_seconds`` call ran most
        recently.

        Returns:
            Resulting numeric value.
        """
        return self._ws_auth_timeout_seconds

    def set_ws_auth_timeout_seconds(self, value: float) -> None:
        """Store a validated WebSocket auth timeout on the app state.

        Mirrors the ``set_max_pending_per_user`` pattern used by the
        ticket store: ``_apply_bridge_config`` resolves the setting
        and calls this setter with the validated value at startup,
        which is then read by the ``/ws`` handler.  Repeated calls
        are allowed and the latest value wins -- tests monkeypatch
        this freely and no state in the mixin enforces a single-shot
        contract.  Bounds mirror the
        ``ApiBridgeConfig.ws_auth_timeout_seconds`` Pydantic field;
        the shared ``WS_AUTH_TIMEOUT_{MIN,MAX}_SECONDS`` constants
        keep the two sites aligned (DRY).

        Raises:
            TypeError: Raised on the corresponding failure path.
            ValueError: Raised on the corresponding failure path.
        """
        import math  # noqa: PLC0415

        from synthorg.settings.bridge_configs import (  # noqa: PLC0415
            WS_AUTH_TIMEOUT_MAX_SECONDS,
            WS_AUTH_TIMEOUT_MIN_SECONDS,
        )

        # ``bool`` is an ``int`` subclass, so ``True``/``False`` would
        # otherwise sail through ``math.isfinite`` and the range check.
        if isinstance(value, bool):
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_auth_timeout_seconds",
                reason="invalid_type",
                provided_type=type(value).__name__,
            )
            msg = f"ws_auth_timeout_seconds must be float, got {type(value).__name__}"
            raise TypeError(msg)

        if not math.isfinite(value):
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_auth_timeout_seconds",
                reason="non_finite",
                provided_value=repr(value),
            )
            msg = f"ws_auth_timeout_seconds must be finite, got {value!r}"
            raise ValueError(msg)
        if value < WS_AUTH_TIMEOUT_MIN_SECONDS or value > WS_AUTH_TIMEOUT_MAX_SECONDS:
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_auth_timeout_seconds",
                reason="out_of_range",
                provided_value=value,
                min_value=WS_AUTH_TIMEOUT_MIN_SECONDS,
                max_value=WS_AUTH_TIMEOUT_MAX_SECONDS,
            )
            msg = (
                "ws_auth_timeout_seconds must be between"
                f" {WS_AUTH_TIMEOUT_MIN_SECONDS} and"
                f" {WS_AUTH_TIMEOUT_MAX_SECONDS} seconds, got {value}"
            )
            raise ValueError(msg)
        self._ws_auth_timeout_seconds = value

    @property
    def ws_frame_timeout_seconds(self) -> int:
        """Per-frame WebSocket receive timeout in seconds.

        Bounded by ``[1, 600]``; defaults to 30. Read once at controller
        construction (read_only_post_init), so the value can be staged
        in tests via ``set_ws_frame_timeout_seconds`` without spinning
        the lifecycle.

        Returns:
            Resulting integer.
        """
        return self._ws_frame_timeout_seconds

    def set_ws_frame_timeout_seconds(self, value: int) -> None:
        """Validate + cache the per-frame WebSocket idle timeout.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        _reject_non_int(value, field="ws_frame_timeout_seconds")
        if not (
            _WS_FRAME_TIMEOUT_MIN_SECONDS <= value <= _WS_FRAME_TIMEOUT_MAX_SECONDS
        ):
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="ws_frame_timeout_seconds",
                reason="out_of_range",
                provided_value=value,
                min_value=_WS_FRAME_TIMEOUT_MIN_SECONDS,
                max_value=_WS_FRAME_TIMEOUT_MAX_SECONDS,
            )
            msg = (
                "ws_frame_timeout_seconds must be between"
                f" {_WS_FRAME_TIMEOUT_MIN_SECONDS} and"
                f" {_WS_FRAME_TIMEOUT_MAX_SECONDS} seconds, got {value}"
            )
            raise ValueError(msg)
        self._ws_frame_timeout_seconds = value

    @property
    def auth_revalidate_window_seconds(self) -> int:
        """Sliding-window length for WS+SSE revalidation failures.

        Returns:
            Resulting integer.
        """
        return self._auth_revalidate_window_seconds

    def set_auth_revalidate_window_seconds(self, value: int) -> None:
        """Validate + cache the revalidation sliding-window length.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        _reject_non_int(value, field="auth_revalidate_window_seconds")
        if not (
            _AUTH_REVALIDATE_WINDOW_MIN_SECONDS
            <= value
            <= _AUTH_REVALIDATE_WINDOW_MAX_SECONDS
        ):
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="auth_revalidate_window_seconds",
                reason="out_of_range",
                provided_value=value,
                min_value=_AUTH_REVALIDATE_WINDOW_MIN_SECONDS,
                max_value=_AUTH_REVALIDATE_WINDOW_MAX_SECONDS,
            )
            msg = (
                "auth_revalidate_window_seconds must be between"
                f" {_AUTH_REVALIDATE_WINDOW_MIN_SECONDS} and"
                f" {_AUTH_REVALIDATE_WINDOW_MAX_SECONDS} seconds,"
                f" got {value}"
            )
            raise ValueError(msg)
        self._auth_revalidate_window_seconds = value

    @property
    def auth_revalidate_max_failures(self) -> int:
        """Max WS+SSE revalidation failures admitted in the window.

        Returns:
            Resulting integer.
        """
        return self._auth_revalidate_max_failures

    def set_auth_revalidate_max_failures(self, value: int) -> None:
        """Validate + cache the revalidation max-failures cap.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        _reject_non_int(value, field="auth_revalidate_max_failures")
        if not (
            _AUTH_REVALIDATE_MAX_FAILURES_MIN
            <= value
            <= _AUTH_REVALIDATE_MAX_FAILURES_MAX
        ):
            logger.warning(
                API_BRIDGE_CONFIG_REJECTED,
                field="auth_revalidate_max_failures",
                reason="out_of_range",
                provided_value=value,
                min_value=_AUTH_REVALIDATE_MAX_FAILURES_MIN,
                max_value=_AUTH_REVALIDATE_MAX_FAILURES_MAX,
            )
            msg = (
                "auth_revalidate_max_failures must be between"
                f" {_AUTH_REVALIDATE_MAX_FAILURES_MIN} and"
                f" {_AUTH_REVALIDATE_MAX_FAILURES_MAX}, got {value}"
            )
            raise ValueError(msg)
        self._auth_revalidate_max_failures = value

    @property
    def has_session_store(self) -> bool:
        """Check whether the session store is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._session_store is not None

    @property
    def session_store(self) -> SessionStore:
        """Return the JWT session store.

        Returns:
            ``SessionStore`` instance.
        """
        return self._require_service(
            self._session_store,
            "session_store",
        )

    def set_session_store(self, store: SessionStore) -> None:
        """Attach the session store (once-only)."""
        self._set_once("_session_store", store, "Session store")

    @property
    def has_lockout_store(self) -> bool:
        """Check whether the lockout store is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._lockout_store is not None

    @property
    def lockout_store(self) -> LockoutStore:
        """Return the account lockout store.

        Returns:
            ``LockoutStore`` instance.
        """
        return self._require_service(
            self._lockout_store,
            "lockout_store",
        )

    def set_lockout_store(self, store: LockoutStore) -> None:
        """Attach the lockout store (once-only)."""
        self._set_once("_lockout_store", store, "Lockout store")

    @property
    def has_refresh_store(self) -> bool:
        """Check whether the refresh-token store is configured.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        return self._refresh_store is not None

    @property
    def refresh_store(self) -> RefreshStore:
        """Return the refresh-token store.

        Returns:
            ``RefreshStore`` instance.
        """
        return self._require_service(
            self._refresh_store,
            "refresh_store",
        )

    def set_refresh_store(self, store: RefreshStore) -> None:
        """Attach the refresh-token store (once-only)."""
        self._set_once("_refresh_store", store, "Refresh store")

    @property
    def user_presence(self) -> UserPresence:
        """Return the user presence tracker (always available).

        Returns:
            ``UserPresence`` instance.
        """
        return self._user_presence

    def set_auth_service(self, service: AuthService) -> None:
        """Attach the auth service (once-only)."""
        self._set_once("_auth_service", service, "Auth service")
