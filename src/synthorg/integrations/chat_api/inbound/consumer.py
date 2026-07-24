"""Long-running Socket-Mode consumer: connect, stream, resume, reconnect.

The consumer owns the inbound loop's lifecycle (``start`` / ``stop``, like
``WebhookEventBridge``): a resident task that, while the kill-switch is on,
resolves the bound Slack connection's app-level token, opens the
Socket-Mode socket, and streams each event to the
:class:`~synthorg.integrations.chat_api.inbound.router.InboundResumeRouter`.
Reconnect-on-drop is delegated to the shared
:class:`~synthorg.core.resilience.general_retry.GeneralRetryHandler`
(never a hand-rolled backoff). The kill-switch
(``tools.chat_inbound_enabled``) is read live per iteration and fail-safes
to DISABLED: an inbound control surface must not self-enable on a settings
outage.
"""

import asyncio
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.resilience.general_retry import GeneralRetryHandler
from synthorg.integrations.chat_api.inbound.models import InboundChatEvent
from synthorg.integrations.chat_api.inbound.router import InboundResumeRouter
from synthorg.integrations.chat_api.inbound.socket_mode import (
    SlackSocketModeClient,
    WsConnector,
    aiohttp_ws_connector,
)
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.errors import ChatApiError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    CHAT_INBOUND_DISABLED,
    CHAT_INBOUND_DISCONNECTED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

# Slack's Socket-Mode gateway and the ``apps.connections.open`` call are
# always on slack.com regardless of any per-connection base_url override,
# so egress is pinned here.
_SLACK_API_BASE: Final[str] = "https://slack.com/api"
_APP_TOKEN_FIELD: Final[str] = "app_token"  # noqa: S105 -- field name, not a secret
_ENABLED_KEY: Final[str] = "chat_inbound_enabled"
_CONNECTION_KEY: Final[str] = "chat_inbound_connection"

_OPEN_TIMEOUT_SECONDS: Final[float] = 15.0
_IDLE_SECONDS: Final[float] = 5.0
_RECONNECT_MAX_ATTEMPTS: Final[int] = 6
_RECONNECT_BASE_SECONDS: Final[float] = 1.0
_RECONNECT_CAP_SECONDS: Final[float] = 30.0
_STOP_TIMEOUT_SECONDS: Final[float] = 10.0


def _stop_was_cancelled() -> bool:
    """Whether the currently-running ``stop()`` was itself cancelled.

    Distinguishes an outer shutdown budget cancelling ``stop()`` (must
    propagate) from the loop task's own cancellation surfacing through the
    shield (expected, must not propagate).

    Returns:
        ``True`` when the current task has a pending cancellation request.
    """
    current = asyncio.current_task()
    return current is not None and current.cancelling() > 0


class ChatInboundConsumer:
    """Resident Socket-Mode inbound loop with a live kill-switch.

    Args:
        connection_catalog: Resolves the bound connection's app token.
        router: Routes each decoded event to its parked approval.
        connector: WebSocket connector (injected for tests; defaults to
            the aiohttp connector).
        config_resolver: Live settings resolver (may be wired post-hoc via
            :meth:`set_config_resolver`).
        clock: Clock seam for the idle/reconnect sleeps.
    """

    __slots__ = (
        "_catalog",
        "_clock",
        "_config_resolver",
        "_connector",
        "_lifecycle_lock",
        "_reconnect_retry",
        "_router",
        "_task",
    )

    def __init__(
        self,
        *,
        connection_catalog: ConnectionCatalog,
        router: InboundResumeRouter,
        connector: WsConnector = aiohttp_ws_connector,
        config_resolver: ConfigResolver | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._catalog = connection_catalog
        self._router = router
        self._connector = connector
        self._config_resolver = config_resolver
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._lifecycle_lock = asyncio.Lock()  # lint-allow: loop-bound-init -- ctx
        self._task: asyncio.Task[None] | None = None
        self._reconnect_retry = GeneralRetryHandler(
            retryable=lambda exc: isinstance(exc, ChatApiError) and exc.retryable,
            max_attempts=_RECONNECT_MAX_ATTEMPTS,
            base=_RECONNECT_BASE_SECONDS,
            cap=_RECONNECT_CAP_SECONDS,
            event=CHAT_INBOUND_DISCONNECTED,
            clock=self._clock,
        )

    def set_config_resolver(self, resolver: ConfigResolver) -> None:
        """Wire the live settings resolver (post-construction)."""
        self._config_resolver = resolver

    async def start(self) -> None:
        """Start the inbound loop (idempotent)."""
        async with self._lifecycle_lock:
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Cancel the inbound loop and await its teardown (idempotent).

        The cancel+await is held under the lifecycle lock so a concurrent
        ``start()`` cannot observe a half-torn-down loop and spawn a second
        Socket-Mode session. If ``stop()`` is itself cancelled (an outer
        shutdown budget elapsing), the loop task is cancelled before the
        cancellation propagates, so it can never outlive the consumer as an
        orphan.

        Raises:
            asyncio.CancelledError: If ``stop()`` is cancelled by an outer
                shutdown budget (propagated after cancelling the loop task).
        """
        async with self._lifecycle_lock:
            task = self._task
            if task is None:
                return
            task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(task), timeout=_STOP_TIMEOUT_SECONDS
                )
            except TimeoutError:
                # The task did not wind down within the budget; leave it
                # cancelled (aiohttp bounds the socket close) so it stops
                # accepting inbound events.
                task.cancel()
                logger.warning(CHAT_INBOUND_DISABLED, reason="stop_timeout")
            except asyncio.CancelledError:
                # Either the loop task's own cancellation surfaced through
                # the shield (expected), or our stop() was cancelled by an
                # outer budget. Ensure the task is cancelled either way, and
                # re-raise only when WE were cancelled so structured
                # cancellation is preserved.
                task.cancel()
                if _stop_was_cancelled():
                    raise
            finally:
                self._task = None

    async def _run_loop(self) -> None:
        """Kill-switch-gated connect/stream/reconnect loop."""
        # lint-allow: long-running-loop-kill-switch -- _resolve_enabled gates below
        while True:
            if not await self._resolve_enabled():
                logger.debug(CHAT_INBOUND_DISABLED, reason="disabled_by_setting")
                await self._clock.sleep(_IDLE_SECONDS)
                continue
            connection_name = await self._resolve_connection_name()
            if connection_name:
                await self._run_session(connection_name)
            await self._clock.sleep(_IDLE_SECONDS)

    async def _run_session(self, connection_name: str) -> None:
        """Connect and stream once, reconnecting transient drops.

        Raises:
            asyncio.CancelledError: If the loop task is cancelled by
                ``stop()`` (propagated so teardown completes promptly).
        """
        try:
            await self._reconnect_retry.execute(
                lambda: self._connect_and_stream(connection_name),
                connection=connection_name,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # A bad token / exhausted reconnect must not kill the loop: log
            # and fall back to the idle re-check so a fixed credential or a
            # re-enabled setting recovers without a restart.
            reraise_critical(exc)
            logger.warning(
                CHAT_INBOUND_DISCONNECTED,
                connection=connection_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _connect_and_stream(self, connection_name: str) -> None:
        """Resolve the app token and stream events until the socket drops.

        Raises:
            ChatApiError: On a transport/auth failure (retryable ones are
                retried by the reconnect handler).
        """
        token = await self._resolve_app_token(connection_name)
        if token is None:
            return
        client = SlackSocketModeClient(
            app_token=token,
            connector=self._connector,
            api_base_url=_SLACK_API_BASE,
            timeout=_OPEN_TIMEOUT_SECONDS,
        )
        await client.stream(on_event=self._safe_route)

    async def _safe_route(self, event: InboundChatEvent) -> None:
        """Route one event, isolating a bad event from the live socket.

        Raises:
            asyncio.CancelledError: If the loop task is cancelled mid-route.
        """
        try:
            await self._router.route(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- one malformed event must not drop
            # the socket and strand every later inbound reply.
            reraise_critical(exc)
            logger.warning(
                CHAT_INBOUND_DISCONNECTED,
                reason="event_route_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _resolve_app_token(self, connection_name: str) -> str | None:
        """Resolve the connection's Socket-Mode app token.

        Returns:
            The app token, or ``None`` when the connection has none
            configured (inbound stays inert for it).
        """
        credentials = await self._catalog.get_credentials(connection_name)
        token = credentials.get(_APP_TOKEN_FIELD)
        if not token:
            logger.debug(
                CHAT_INBOUND_DISABLED,
                reason="missing_app_token",
                connection=connection_name,
            )
            return None
        return token

    async def _resolve_enabled(self) -> bool:
        """Resolve the kill-switch, fail-safe to DISABLED.

        Returns:
            The live ``tools.chat_inbound_enabled`` value, or ``False``
            when no resolver is wired or the read fails (an inbound
            control surface must never self-enable).

        Raises:
            asyncio.CancelledError: If cancelled during the resolver read.
        """
        if self._config_resolver is None:
            return False
        try:
            return await self._config_resolver.get_bool(
                SettingNamespace.TOOLS.value, _ENABLED_KEY
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                CHAT_INBOUND_DISABLED,
                reason="resolver_failed_fail_closed",
                error_type=type(exc).__name__,
            )
            return False

    async def _resolve_connection_name(self) -> str:
        """Resolve the bound Slack connection name.

        Returns:
            The configured connection name, or ``""`` when unset or the
            read fails (inbound stays inert).

        Raises:
            asyncio.CancelledError: If cancelled during the resolver read.
        """
        if self._config_resolver is None:
            return ""
        try:
            return (
                await self._config_resolver.get_str(
                    SettingNamespace.TOOLS.value, _CONNECTION_KEY
                )
            ).strip()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            return ""


__all__ = ["ChatInboundConsumer"]
