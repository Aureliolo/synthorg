"""OAuth token lifecycle manager.

Background service that monitors OAuth connections and refreshes
tokens before they expire.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.iso_datetime import parse_iso_assume_utc
from synthorg.core.lifecycle_constants import DEFAULT_DRAIN_TIMEOUT_SECONDS
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    OAuthToken,
)
from synthorg.integrations.errors import (
    IntegrationLifecycleConflictError,
    TokenRefreshFailedError,
)
from synthorg.integrations.oauth.flows.authorization_code import (
    AuthorizationCodeFlow,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.background_tasks import log_task_exceptions
from synthorg.observability.events.integrations import (
    OAUTH_TOKEN_EXPIRED,
    OAUTH_TOKEN_REFRESH_FAILED,
    OAUTH_TOKEN_REFRESHED,
)
from synthorg.observability.events.settings import SETTINGS_FETCH_FAILED
from synthorg.settings.enums import SettingNamespace

if TYPE_CHECKING:
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)
_DEFAULT_REFRESH_THRESHOLD_SECONDS: Final[int] = 300
_DEFAULT_CHECK_INTERVAL_SECONDS: Final[int] = 60


class OAuthTokenManager:
    """Monitors OAuth connections and refreshes tokens proactively.

    Runs as a background asyncio task, checking all OAuth2
    connections and refreshing tokens that are about to expire.

    Args:
        catalog: The connection catalog.
        refresh_threshold_seconds: Refresh tokens expiring within
            this window.
        check_interval_seconds: How often to check for expiring tokens.
        config_resolver: Optional ConfigResolver used to resolve the
            operator-tuned OAuth HTTP timeout
            (``integrations.oauth_http_timeout_seconds``, restart
            required) plus the sweep interval
            (``integrations.oauth_token_check_interval_seconds``) and
            refresh window
            (``integrations.oauth_token_refresh_threshold_seconds``).
            All are resolved once at :meth:`start`; when the resolver
            is absent or a lookup fails, the constructor default
            (equal to the registered default) is kept.
    """

    def __init__(
        self,
        catalog: ConnectionCatalog,
        *,
        refresh_threshold_seconds: int = _DEFAULT_REFRESH_THRESHOLD_SECONDS,
        check_interval_seconds: int = _DEFAULT_CHECK_INTERVAL_SECONDS,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        self._catalog = catalog
        self._threshold = timedelta(seconds=refresh_threshold_seconds)
        self._interval = check_interval_seconds
        self._config_resolver = config_resolver
        self._task: asyncio.Task[None] | None = None
        self._flow = AuthorizationCodeFlow()
        # Eager init: stop() must be safe before any start() call.
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- see.
        # Survives a timed-out stop so a later start() cannot stack a
        # second refresh loop on the orphaned one (canonical lifecycle
        # pattern, see docs/reference/lifecycle-sync.md).
        self._stop_failed = False
        self._stop_drain_timeout_seconds: float = DEFAULT_DRAIN_TIMEOUT_SECONDS

    def set_config_resolver(self, resolver: ConfigResolver) -> None:
        """Inject the ConfigResolver after construction.

        :class:`OAuthTokenManager` is instantiated before ``AppState``
        in :func:`synthorg.api.app.create_app` (because ``AppState``
        takes it as a constructor argument), so the resolver is not
        available at construction time. The API startup hook calls
        this setter after ``AppState`` is built and before
        :meth:`start` to ensure refresh calls honour the operator-tuned
        HTTP timeout.
        """
        self._config_resolver = resolver

    async def _resolve_flow_timeout(self) -> None:
        """Rebuild the flow with the operator-tuned HTTP timeout.

        Called once inside :meth:`start` before the refresh loop spawns
        so refreshes use the resolved value. A settings outage is
        non-fatal -- the flow keeps its built-in default.
        """
        if self._config_resolver is None:
            return
        try:
            timeout = await self._config_resolver.get_float(
                SettingNamespace.INTEGRATIONS.value,
                "oauth_http_timeout_seconds",
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Logging this as OAUTH_TOKEN_REFRESH_FAILED would
            # falsely mark an OAuth refresh as failed and trip any
            # alerting on that event. Emit on the settings-fetch
            # channel at INFO instead, since the manager will keep
            # using the flow's built-in timeout default.
            logger.info(
                SETTINGS_FETCH_FAILED,
                namespace=SettingNamespace.INTEGRATIONS.value,
                key="oauth_http_timeout_seconds",
                error=(
                    "failed to resolve oauth_http_timeout_seconds;"
                    f" keeping flow default ({type(exc).__name__})"
                ),
            )
            return
        self._flow = AuthorizationCodeFlow(http_timeout_seconds=timeout)

    async def _resolve_loop_tuning(self) -> None:
        """Resolve the operator-tuned sweep interval and refresh window.

        Called once inside :meth:`start` before the refresh loop spawns.
        A settings outage is non-fatal: each value keeps the
        constructor default (which equals the registered default), so a
        backend outage never silently disables the sweep.
        """
        if self._config_resolver is None:
            return
        for key, apply in (
            ("oauth_token_check_interval_seconds", self._apply_interval),
            ("oauth_token_refresh_threshold_seconds", self._apply_threshold),
        ):
            try:
                value = await self._config_resolver.get_int(
                    SettingNamespace.INTEGRATIONS.value,
                    key,
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.info(
                    SETTINGS_FETCH_FAILED,
                    namespace=SettingNamespace.INTEGRATIONS.value,
                    key=key,
                    error=(
                        f"failed to resolve {key}; keeping default"
                        f" ({type(exc).__name__})"
                    ),
                )
                continue
            apply(value)

    def _apply_interval(self, seconds: int) -> None:
        """Update the refresh-loop poll interval (seconds)."""
        self._interval = seconds

    def _apply_threshold(self, seconds: int) -> None:
        """Update the pre-expiry refresh threshold (seconds)."""
        self._threshold = timedelta(seconds=seconds)

    async def start(self) -> None:
        """Start the background refresh loop.

        Raises:
            IntegrationLifecycleConflictError: If the manager was
                previously stopped with a timeout and is unrestartable.
        """
        async with self._lifecycle_lock:
            if self._stop_failed:
                msg = (
                    "OAuthTokenManager is unrestartable after a timed-out "
                    "stop; construct a fresh manager instead"
                )
                logger.warning(
                    OAUTH_TOKEN_REFRESH_FAILED,
                    error=msg,
                    note="unrestartable",
                )
                raise IntegrationLifecycleConflictError(msg)
            if self._task is not None:
                return
            await self._resolve_flow_timeout()
            await self._resolve_loop_tuning()
            self._task = asyncio.create_task(self._refresh_loop())
            logger.info(
                OAUTH_TOKEN_REFRESHED,
                has_refresh=False,
                note="token manager started",
            )

    async def stop(self) -> None:
        """Stop the background refresh loop.

        Raises:
            TimeoutError: If the refresh-task drain exceeds
                ``_stop_drain_timeout_seconds``; the manager is then
                marked unrestartable.
        """
        async with self._lifecycle_lock:
            task = self._task
            if task is None:
                return
            task.cancel()

            async def _drain() -> None:
                """Await the cancelled refresh task, swallowing its cancellation."""
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                    reraise_critical(exc)
                    logger.warning(
                        OAUTH_TOKEN_REFRESH_FAILED,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                        note="shutdown",
                    )

            drain_task: asyncio.Task[None] = asyncio.create_task(_drain())
            try:
                await asyncio.wait_for(
                    asyncio.shield(drain_task),
                    timeout=self._stop_drain_timeout_seconds,
                )
            except TimeoutError:
                self._stop_failed = True
                logger.error(
                    OAUTH_TOKEN_REFRESH_FAILED,
                    error=(
                        "stop exceeded hard deadline; token manager "
                        "marked unrestartable"
                    ),
                    timeout_seconds=self._stop_drain_timeout_seconds,
                )
                # Log the orphaned drain's eventual outcome (it keeps running
                # past the deadline) rather than dropping it silently.
                drain_task.add_done_callback(
                    log_task_exceptions(
                        logger,
                        OAUTH_TOKEN_REFRESH_FAILED,
                        note="orphaned_drain_after_timeout",
                    )
                )
                raise
            self._task = None

    async def _refresh_loop(self) -> None:
        """Periodically check and refresh expiring tokens.

        Raises:
            asyncio.CancelledError: If the refresh task is cancelled via
                ``stop()`` or direct task cancellation.
        """
        # lint-allow: long-running-loop-kill-switch -- stop()/cancel drives shutdown.
        while True:
            try:
                await self._check_and_refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    OAUTH_TOKEN_REFRESH_FAILED,
                    exc,
                    reason="unexpected error in refresh loop",
                )
            await asyncio.sleep(self._interval)

    async def _check_and_refresh(self) -> None:
        """Check all OAuth connections for expiring tokens."""
        all_connections = await self._catalog.list_all()
        now = datetime.now(UTC)
        threshold = now + self._threshold

        for conn in all_connections:
            if conn.auth_method != AuthMethod.OAUTH2:
                continue
            # Token expiry is tracked via connection metadata, which
            # is externally editable. Guard everything that could
            # escape as a ``TypeError`` (non-string value) or
            # comparison failure (naive datetime) so a single bad
            # connection does not abort the sweep and skip every
            # later OAuth connection.
            expiry_raw = conn.metadata.get("token_expires_at")
            if not isinstance(expiry_raw, str) or not expiry_raw.strip():
                continue
            try:
                expiry = parse_iso_assume_utc(expiry_raw.strip())
            except TypeError, ValueError:
                logger.warning(
                    OAUTH_TOKEN_REFRESH_FAILED,
                    connection_name=conn.name,
                    error="malformed token_expires_at metadata",
                    value=expiry_raw,
                )
                continue

            try:
                is_expired = expiry <= now
                is_in_window = expiry <= threshold
            except TypeError:
                logger.warning(
                    OAUTH_TOKEN_REFRESH_FAILED,
                    connection_name=conn.name,
                    error="token_expires_at comparison failed",
                )
                continue

            if is_expired:
                logger.warning(
                    OAUTH_TOKEN_EXPIRED,
                    connection_name=conn.name,
                )
                await self._catalog.update_health(
                    conn.name,
                    status=ConnectionStatus.DEGRADED,
                    checked_at=now,
                )
            elif is_in_window:
                await self._refresh_one(conn, now)

    async def _refresh_one(self, conn: Connection, now: datetime) -> None:
        """Refresh tokens for one connection and persist them.

        Any failure is logged and the connection is flipped to
        ``DEGRADED``; exceptions are swallowed here so one failing
        connection never crashes the refresh loop.
        """
        try:
            credentials = await self._catalog.get_credentials(conn.name)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                OAUTH_TOKEN_REFRESH_FAILED,
                connection_name=conn.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="credential load failed",
            )
            await self._catalog.update_health(
                conn.name,
                status=ConnectionStatus.DEGRADED,
                checked_at=now,
            )
            return

        token_url = credentials.get("token_url", "")
        client_id = credentials.get("client_id", "")
        client_secret = credentials.get("client_secret", "")
        refresh_token = credentials.get("refresh_token", "")
        if not (token_url and client_id and client_secret and refresh_token):
            logger.warning(
                OAUTH_TOKEN_REFRESH_FAILED,
                connection_name=conn.name,
                reason="missing refresh credentials",
            )
            await self._catalog.update_health(
                conn.name,
                status=ConnectionStatus.DEGRADED,
                checked_at=now,
            )
            return

        try:
            refreshed = await self._flow.refresh_token(
                token_url=token_url,
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
            )
        except TokenRefreshFailedError:
            logger.warning(
                OAUTH_TOKEN_REFRESH_FAILED,
                connection_name=conn.name,
            )
            await self._catalog.update_health(
                conn.name,
                status=ConnectionStatus.DEGRADED,
                checked_at=now,
            )
            return

        if not await self._persist_refreshed_tokens(conn, refreshed, now):
            return

        logger.info(
            OAUTH_TOKEN_REFRESHED,
            connection_name=conn.name,
            has_refresh=refreshed.refresh_token is not None,
            note="proactive refresh completed",
        )

    async def _persist_refreshed_tokens(
        self,
        conn: Connection,
        refreshed: OAuthToken,
        now: datetime,
    ) -> bool:
        """Persist refreshed tokens; ``False`` if the write failed.

        On failure the connection is flipped to ``DEGRADED`` so an
        operator notices, and the exception is swallowed so the sweep
        continues with the next connection. The traceback is
        deliberately not logged: the stack frames here hold the OAuth
        client secret and refresh token, so only a redacted
        description is emitted.

        Returns:
            ``True`` when tokens were persisted (and metadata updated if
            an expiry was present); ``False`` when the access token was
            empty or the persistence write failed.
        """
        access_token = refreshed.access_token
        if not access_token:
            logger.warning(
                OAUTH_TOKEN_REFRESH_FAILED,
                connection_name=conn.name,
                reason="refresh returned no access_token",
            )
            # Treat an empty refresh result as a failure path so the
            # connection's health flips to ``DEGRADED`` and an operator
            # notices it, instead of silently leaving the old expired
            # token in place.
            await self._catalog.update_health(
                conn.name,
                status=ConnectionStatus.DEGRADED,
                checked_at=now,
            )
            return False
        try:
            await self._catalog.store_oauth_tokens(
                conn.name,
                access_token=access_token,
                refresh_token=refreshed.refresh_token,
            )
            if refreshed.expires_at is not None:
                meta_updates = dict(conn.metadata)
                meta_updates["token_expires_at"] = refreshed.expires_at.isoformat()
                await self._catalog.update(conn.name, metadata=meta_updates)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                OAUTH_TOKEN_REFRESH_FAILED,
                connection_name=conn.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                reason="failed to persist refreshed tokens",
            )
            try:
                await self._catalog.update_health(
                    conn.name,
                    status=ConnectionStatus.DEGRADED,
                    checked_at=now,
                )
            except Exception as health_exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(health_exc)
                logger.warning(
                    OAUTH_TOKEN_REFRESH_FAILED,
                    connection_name=conn.name,
                    error_type=type(health_exc).__name__,
                    error=safe_error_description(health_exc),
                    reason="update_health failed after persistence failure",
                )
            return False
        return True
