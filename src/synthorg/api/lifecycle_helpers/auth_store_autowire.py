# module-kind: code
"""Auth-store auto-wiring for the startup lifecycle.

The session, lockout, and refresh-token stores live on the connected
persistence backend (sessions / refresh tokens are repository
properties; lockouts are built via ``build_lockouts(auth_config)`` so
they pick up the operator's threshold / window / duration policy). They
wire once at ``on_startup`` after ``persistence.connect()``; each slice
field already set short-circuits, so a re-entered lifespan does not
re-load revoked / locked state.
"""

from typing import TYPE_CHECKING

from synthorg.api.api_core_state import ApiCoreStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.api import API_APP_STARTUP

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.persistence.auth_protocol import (
        LockoutRepository,
        RefreshTokenRepository,
        SessionRepository,
    )
    from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


async def wire_auth_stores(
    app_state: AppState,
    persistence: PersistenceBackend,
) -> None:
    """Wire the session, lockout, and refresh-token stores.

    Loads the revoked-session and locked-account state into memory as
    part of wiring so the auth middleware sees the durable state from
    the first request. Lockout wiring is best-effort (logged, non-fatal)
    so a lockout-policy build failure does not abort startup; session
    and refresh-token wiring failures abort startup so the API never
    serves requests with partially-wired auth.
    """
    if app_state.slice(ApiCoreStateSlice).session_store is None:
        try:
            session_store: SessionRepository = persistence.sessions
            await session_store.load_revoked()
            app_state.wire(ApiCoreStateSlice, session_store=session_store)
            logger.info(
                API_APP_STARTUP,
                note="Session store initialized",
                backend=type(session_store).__name__,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                API_APP_STARTUP,
                exc,
                note="Session store initialization failed",
            )
            raise

    auth_cfg = app_state.config.api.auth if app_state.config is not None else None
    if (
        auth_cfg is not None
        and app_state.slice(ApiCoreStateSlice).lockout_store is None
    ):
        try:
            lockout_store: LockoutRepository = persistence.build_lockouts(auth_cfg)
            await lockout_store.load_locked()
            app_state.wire(ApiCoreStateSlice, lockout_store=lockout_store)
            logger.info(
                API_APP_STARTUP,
                note="Lockout store initialized",
                backend=type(lockout_store).__name__,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                API_APP_STARTUP,
                exc,
                note="Lockout store initialization failed",
            )

    if app_state.slice(ApiCoreStateSlice).refresh_store is None:
        try:
            refresh_store: RefreshTokenRepository = persistence.refresh_tokens
            app_state.wire(ApiCoreStateSlice, refresh_store=refresh_store)
            logger.info(
                API_APP_STARTUP,
                note="Refresh-token store initialized",
                backend=type(refresh_store).__name__,
            )
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                API_APP_STARTUP,
                exc,
                note="Refresh-token store initialization failed",
            )
            raise
