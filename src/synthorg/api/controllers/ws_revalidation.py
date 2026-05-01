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
from typing import Any

from litestar import WebSocket  # noqa: TC002

from synthorg.api.auth.config import WS_REVALIDATE_INTERVAL_SECONDS
from synthorg.api.auth.models import AuthenticatedUser  # noqa: TC001
from synthorg.api.guards import _READ_ROLES
from synthorg.engine.classification.sinks import _SlidingWindowRateLimiter
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_WS_REVALIDATION_BUDGET_EXHAUSTED,
    API_WS_TRANSPORT_ERROR,
)
from synthorg.observability.events.security import SECURITY_SESSION_REVOKED

logger = get_logger(__name__)

# Application-layer WS close codes (RFC 6455 §7.4.2: 4000-4999). Mirror
# in ``ws.py``; kept here for the revalidation close paths.
_WS_CLOSE_FORBIDDEN: int = 4003
_WS_CLOSE_SERVER_ERROR: int = 4011


async def _close_socket_safely(
    socket: WebSocket[Any, Any, Any],
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
    except Exception as exc:
        logger.warning(
            API_WS_TRANSPORT_ERROR,
            reason="socket_close_failed_during_revoke",
            client=str(socket.client),
            close_code=code,
            close_reason=reason,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def _periodic_revalidate(
    socket: WebSocket[Any, Any, Any],
    user: AuthenticatedUser,
    *,
    interval_seconds: int = WS_REVALIDATE_INTERVAL_SECONDS,
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
    ``api.ws_revalidation_window_seconds`` (60s) and
    ``api.ws_revalidation_max_failures`` (5).  At construction time
    the parent passes the values resolved from ``AppState`` so the
    limiter window matches operator config.
    """
    app_state = socket.app.state["app_state"]
    window = (
        failure_window_seconds
        if failure_window_seconds is not None
        else app_state.ws_revalidation_window_seconds
    )
    max_failures = (
        failure_max
        if failure_max is not None
        else app_state.ws_revalidation_max_failures
    )
    failure_limiter = _SlidingWindowRateLimiter(
        max_events=max_failures,
        window_seconds=float(window),
    )
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        try:
            db_user = await app_state.persistence.users.get(user.user_id)
        except Exception as exc:
            admitted = await failure_limiter.take(user.user_id)
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
            if not admitted:
                # TRY400: this is a budget-exhaustion event that
                # already includes the precipitating exception via the
                # warning above; an exception() trace here would
                # duplicate that and bury the structured fields.
                logger.error(  # noqa: TRY400
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
                return
            continue

        revoke_reason = _revocation_reason(db_user, user, app_state)
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


def _revocation_reason(
    db_user: object | None,
    user: AuthenticatedUser,
    app_state: Any,
) -> str | None:
    """Return the rejection reason or None when still authorised.

    Three independent gates: the user record (deleted / role-missing /
    role-demoted), the role allowlist, and the session-revocation
    set. The session check uses the JWT JTI captured at ticket-issue
    time and consults the in-memory revoked-session set published by
    ``session_store`` so an admin's ``DELETE /sessions/{jti}`` kicks
    the live connection out without waiting for token expiry.
    """
    if db_user is None:
        return "user_deleted"
    role = getattr(db_user, "role", None)
    if role is None:
        return "user_role_missing"
    if role not in _READ_ROLES:
        return "role_demoted"
    if (
        user.session_id is not None
        and app_state.has_session_store
        and app_state.session_store.is_revoked(user.session_id)
    ):
        return "session_revoked"
    return None
