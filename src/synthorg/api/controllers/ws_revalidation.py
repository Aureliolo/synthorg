"""WebSocket session revalidation + safe-close helpers.

Extracted from ``ws.py`` to keep the controller module under the 800-line
size budget. The receive loop in ``ws.py`` imports
:func:`_periodic_revalidate` for the background task that re-checks the
authenticated user every interval; and :func:`_close_socket_safely` for
best-effort socket teardown that swallows transport-layer errors.

Public surface (re-exported from ``ws.py``):

* :func:`_close_socket_safely`
* :func:`_periodic_revalidate`
* :func:`_revocation_reason`
"""

import asyncio

from litestar import WebSocket
from litestar.datastructures import State

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.api.guards import _READ_ROLES
from synthorg.api.state import AppState
from synthorg.core.auth.config import AUTH_REVALIDATE_INTERVAL_SECONDS
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.resilience import build_revalidation_limiter
from synthorg.core.resilience.sliding_window import SlidingWindowEventLimiter
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_WS_REVALIDATION_BUDGET_EXHAUSTED,
    API_WS_TRANSPORT_ERROR,
)
from synthorg.observability.events.security import SECURITY_SESSION_REVOKED
from synthorg.persistence.state import persistence_of

logger = get_logger(__name__)

# Application-layer WS close codes (RFC 6455 §7.4.2: 4000-4999). Mirror
# in ``ws.py``; kept here for the revalidation close paths.
_WS_CLOSE_FORBIDDEN: int = 4003
_WS_CLOSE_SERVER_ERROR: int = 4011


async def _close_socket_safely(
    socket: WebSocket[object, object, State],
    *,
    code: int,
    reason: str,
) -> None:
    """Best-effort close that logs but does not propagate teardown errors.

    The socket may already be torn down (client disconnected, network
    blip), but we still want the revocation decision recorded AND the
    close failure logged so operators can diagnose half-open sockets
    after a session-revocation event.
    """
    try:
        await socket.close(code=code, reason=reason)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_WS_TRANSPORT_ERROR,
            reason="socket_close_failed_during_revoke",
            client=str(socket.client),
            close_code=code,
            close_reason=reason,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _admit_or_close_on_budget(
    socket: WebSocket[object, object, State],
    failure_limiter: SlidingWindowEventLimiter,
    user: AuthenticatedUser,
    *,
    window: int,
    max_failures: int,
) -> bool:
    """Take one failure-budget token; close + return True when exhausted.

    Shared by the user-record and API-key revalidation read paths so a
    transient persistence error in either is absorbed by the same sliding
    window before the connection is torn down with the server-error code.

    Returns:
        ``True`` when the budget is exhausted and the socket was closed
        (the caller must return), ``False`` when the failure was admitted
        and polling should continue.
    """
    admitted = await failure_limiter.take(user.user_id)
    if not admitted:
        logger.error(
            API_WS_REVALIDATION_BUDGET_EXHAUSTED,
            client=str(socket.client),
            user_id=user.user_id,
            window_seconds=window,
            max_failures=max_failures,
        )
        await _close_socket_safely(
            socket,
            code=_WS_CLOSE_SERVER_ERROR,
            reason="Revalidation backend unavailable",
        )
        return True
    return False


async def _periodic_revalidate(
    socket: WebSocket[object, object, State],
    user: AuthenticatedUser,
    *,
    interval_seconds: int = AUTH_REVALIDATE_INTERVAL_SECONDS,
    failure_window_seconds: int | None = None,
    failure_max: int | None = None,
) -> None:
    """Re-load the user every *interval_seconds* and close on revocation.

    Bounds the post-revocation window to one tick: an admin who
    deletes the user, demotes the role below ``_READ_ROLES``, or
    revokes the session sees the WS close within ``interval_seconds``
    rather than at next disconnect.

    Persistence failures are tracked through a sliding window
    (``failure_window_seconds`` / ``failure_max``) rather than a
    streak counter that resets on success: a flaky persistence
    layer that returns one good response between every failure
    cluster could otherwise hold a stale-auth WS open indefinitely.
    Once ``failure_max`` failures are admitted within the window,
    the connection is closed with the server-error code so the
    client can reconnect against a healthy replica.

    The defaults track the registered settings
    ``api.auth_revalidate_window_seconds`` (60s) and
    ``api.auth_revalidate_max_failures`` (5), shared with the SSE
    revalidation loop.  At construction time the parent passes the
    values resolved from ``AppState`` so the limiter window matches
    operator config.
    """
    app_state = socket.app.state["app_state"]
    window = (
        failure_window_seconds
        if failure_window_seconds is not None
        else app_state.ws_auth_limits.auth_revalidate_window_seconds
    )
    max_failures = (
        failure_max
        if failure_max is not None
        else app_state.ws_auth_limits.auth_revalidate_max_failures
    )
    # The loop performs one persistence check per ``interval_seconds``;
    # the shared builder clamps the window so ``max_failures`` consecutive
    # failed ticks fall inside it (fail-closed) while isolated old failures
    # still age out. The SSE ``_build_revalidation_limiter`` path uses the
    # same builder so the two stay in lockstep.
    failure_limiter = build_revalidation_limiter(
        max_failures=max_failures,
        window_seconds=window,
        interval_seconds=interval_seconds,
    )
    # lint-allow: long-running-loop-kill-switch -- per-connection revalidate.
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            db_user = await persistence_of(app_state).users.get(user.user_id)
            revoke_reason, ok = await _revocation_reason(db_user, user, app_state)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_WS_TRANSPORT_ERROR,
                reason="revalidate_persistence_error",
                client=str(socket.client),
                user_id=user.user_id,
                window_seconds=window,
                max_failures=max_failures,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            if await _admit_or_close_on_budget(
                socket,
                failure_limiter,
                user,
                window=window,
                max_failures=max_failures,
            ):
                return
            continue

        if not ok:
            # The API-key revalidation read failed transiently (the helper
            # already logged the cause); route it through the same failure
            # budget as the user-record read above rather than tearing down.
            if await _admit_or_close_on_budget(
                socket,
                failure_limiter,
                user,
                window=window,
                max_failures=max_failures,
            ):
                return
            continue

        if revoke_reason is not None:
            logger.info(
                SECURITY_SESSION_REVOKED,
                client=str(socket.client),
                user_id=user.user_id,
                reason=revoke_reason,
                trigger="ws_periodic_revalidate",
            )
            await _close_socket_safely(
                socket,
                code=_WS_CLOSE_FORBIDDEN,
                reason=f"Session revoked ({revoke_reason})",
            )
            return


async def _revocation_reason(
    db_user: object | None,
    user: AuthenticatedUser,
    app_state: AppState,
) -> tuple[str | None, bool]:
    """Return ``(reason, ok)``: reason is None when still authorised.

    Four independent gates: the user record (deleted / role-missing /
    role-demoted), the role allowlist, the JWT session-revocation set,
    and -- for API-key-authenticated connections (no JWT session id) --
    the originating API key itself (revoked / expired). The session check
    uses the JWT JTI captured at ticket-issue time and consults the
    in-memory revoked-session set published by ``session_store`` so an
    admin's ``DELETE /sessions/{jti}`` kicks the live connection out
    without waiting for token expiry.

    ``ok`` is False only when the API-key persistence call itself failed
    (transient backend error); the caller admits an ``ok=False`` tick to
    the shared sliding-window limiter rather than tearing the socket down.
    Mirrors the SSE ``_user_revocation_reason`` gate so the two transports
    expire credentials symmetrically.

    Returns:
        Tuple of ``(reason, ok)``, where ``reason`` may be ``None``.
    """
    if db_user is None:
        return "user_deleted", True
    role = getattr(db_user, "role", None)
    if role is None:
        return "user_role_missing", True
    if role not in _READ_ROLES:
        return "role_demoted", True
    session_store = app_state.slice(ApiCoreStateSlice).session_store
    if (
        user.session_id is not None
        and session_store is not None
        and session_store.is_revoked(user.session_id)
    ):
        return "session_revoked", True
    # API-key connections carry no JWT jti; the session-revocation set never
    # covers them. Re-inspect the originating key so revocation / expiry
    # tears the socket down within one revalidation interval.
    if user.session_id is None and user.api_key_id is not None:
        return await _api_key_revocation_reason(app_state, user.api_key_id)
    return None, True


async def _api_key_revocation_reason(
    app_state: AppState,
    api_key_id: str,
) -> tuple[str | None, bool]:
    """Return ``(reason, ok)`` for an API-key-authenticated WS connection.

    Re-fetches the API key by id and reports ``"api_key_revoked"`` when the
    record is missing or revoked, ``"api_key_expired"`` once ``expires_at``
    has passed (compared against the injected clock). A transient backend
    error yields ``ok=False`` so the caller admits it to the shared
    sliding-window limiter rather than tearing down. Mirrors the SSE
    ``_api_key_revocation_reason``.

    Returns:
        Tuple of ``(reason, ok)``, where ``reason`` may be ``None``.
    """
    try:
        api_key = await persistence_of(app_state).api_keys.get(api_key_id)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_WS_TRANSPORT_ERROR,
            reason="revalidate_api_key_persistence_error",
            api_key_id=api_key_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None, False
    if api_key is None or api_key.revoked:
        return "api_key_revoked", True
    if api_key.expires_at is not None and api_key.expires_at <= app_state.clock.now():
        return "api_key_expired", True
    return None, True
