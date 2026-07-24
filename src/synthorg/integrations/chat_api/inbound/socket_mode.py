"""Slack Socket-Mode WebSocket client.

Two steps: resolve a short-lived ``wss://`` gateway URL from the app-level
token via the ``apps.connections.open`` Web API (httpx, host-pinned to
slack.com like the outbound factory), then stream events off that socket,
acknowledging every envelope so Slack does not re-deliver. The WebSocket
transport is injected (:class:`WsConnector`) so the decode + ack + dispatch
logic is unit-testable with a canned frame stream and no live socket; the
default connector uses aiohttp. Reconnect/backoff is the consumer's job
(via the shared resilience handler), so :meth:`stream` returns cleanly on a
server ``disconnect`` frame rather than looping.
"""

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final, Protocol

import httpx

from synthorg.core.normalization import normalize_base_url
from synthorg.integrations.chat_api.inbound.decode import decode_frame
from synthorg.integrations.chat_api.inbound.models import InboundChatEvent
from synthorg.integrations.errors import (
    ChatApiAuthError,
    ChatApiError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    CHAT_API_ENVELOPE_FAILED,
    CHAT_API_REQUEST_FAILED,
    CHAT_INBOUND_CONNECTED,
)

logger = get_logger(__name__)

_OPEN_METHOD: Final[str] = "apps.connections.open"
_AUTH_ERRORS: Final[frozenset[str]] = frozenset(
    {"invalid_auth", "not_authed", "token_revoked", "missing_scope", "no_permission"},
)


class WsSession(Protocol):
    """A live Socket-Mode WebSocket: iterate frames, acknowledge envelopes."""

    def __aiter__(self) -> AsyncIterator[Mapping[str, object]]:
        """Yield each decoded JSON frame as it arrives."""
        ...

    async def ack(self, envelope_id: str) -> None:
        """Acknowledge one envelope so Slack stops re-delivering it."""
        ...


#: Opens a WebSocket session against a resolved ``wss://`` URL.
WsConnector = Callable[[str], AbstractAsyncContextManager[WsSession]]

#: Called with each routable inbound event.
EventHandler = Callable[[InboundChatEvent], Awaitable[None]]


class SlackSocketModeClient:
    """Resolve the Socket-Mode gateway and stream inbound events.

    Args:
        app_token: The Slack app-level token (``xapp-...``); header auth
            only, never logged.
        connector: Opens the WebSocket for a resolved URL (injected for
            tests; defaults to the aiohttp connector).
        api_base_url: The Slack API base (host-pinned by the caller).
        timeout: Per-request timeout for the open call, in seconds.
    """

    def __init__(
        self,
        *,
        app_token: str,
        connector: WsConnector,
        api_base_url: str,
        timeout: float,
    ) -> None:
        if not app_token:
            msg = "SlackSocketModeClient requires a non-blank app_token"
            raise ValueError(msg)
        if timeout <= 0:
            msg = "SlackSocketModeClient timeout must be positive"
            raise ValueError(msg)
        self._app_token = app_token
        self._connector = connector
        self._api_base_url = normalize_base_url(api_base_url)
        self._timeout = timeout

    async def stream(self, *, on_event: EventHandler) -> None:
        """Open the socket and dispatch events until it disconnects.

        Returns when Slack sends a ``disconnect`` frame or the socket
        closes; the caller reconnects. An event envelope is acked only
        AFTER its event is dispatched: the resume dispatcher is idempotent
        (``save_if_pending`` CAS), so at-least-once delivery (a re-delivery
        on a crash between receipt and ack) is strictly safer than losing a
        human decision. A disconnect envelope is still acked before return
        so it is not re-delivered on reconnect.

        Raises:
            ChatApiAuthError: The app token was rejected.
            ChatApiError: The gateway URL could not be resolved.
        """
        url = await self._open_socket_url()
        logger.info(CHAT_INBOUND_CONNECTED)
        async with self._connector(url) as session:
            async for frame in session:
                decoded = decode_frame(frame)
                if decoded.disconnect:
                    if decoded.envelope_id:
                        await session.ack(decoded.envelope_id)
                    return
                if decoded.event is not None:
                    await on_event(decoded.event)
                if decoded.envelope_id:
                    await session.ack(decoded.envelope_id)

    async def _open_socket_url(self) -> str:
        """Resolve the short-lived ``wss://`` gateway URL.

        Returns:
            The gateway URL to open the WebSocket against.

        Raises:
            ChatApiAuthError: The Slack ``ok=false`` code is an auth failure.
            ChatApiError: On transport failure or a malformed response.
        """
        try:
            async with httpx.AsyncClient(
                base_url=self._api_base_url,
                headers={"Authorization": f"Bearer {self._app_token}"},
                timeout=self._timeout,
            ) as client:
                resp = await client.post(_OPEN_METHOD)
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning(
                CHAT_API_REQUEST_FAILED,
                action="open socket mode",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "transport error opening the Slack Socket-Mode connection"
            raise ChatApiError(msg) from exc
        except ValueError as exc:
            # A non-JSON body would otherwise escape as a bare decode error,
            # breaking the ChatApiError contract this method documents.
            logger.warning(
                CHAT_API_ENVELOPE_FAILED,
                action="open socket mode",
                slack_error="invalid_json",
            )
            msg = "Slack Socket-Mode open reply was not valid JSON"
            raise ChatApiError(msg) from exc
        return _url_from(data)


def _url_from(data: object) -> str:
    """Return the gateway URL from an ``apps.connections.open`` reply.

    Returns:
        The ``wss://`` gateway URL.

    Raises:
        ChatApiAuthError: The Slack error code is an auth/scope failure.
        ChatApiError: A malformed reply or any other ``ok=false``.
    """
    if not isinstance(data, Mapping) or data.get("ok") is not True:
        error = data.get("error") if isinstance(data, Mapping) else None
        code = error if isinstance(error, str) else "unknown"
        logger.warning(
            CHAT_API_ENVELOPE_FAILED, action="open socket mode", slack_error=code
        )
        if code in _AUTH_ERRORS:
            msg = f"Slack rejected the app token: {code}"
            raise ChatApiAuthError(msg)
        msg = f"Slack failed to open a Socket-Mode connection: {code}"
        raise ChatApiError(msg)
    url = data.get("url")
    if not isinstance(url, str) or not url:
        msg = "Slack Socket-Mode reply omitted the gateway url"
        raise ChatApiError(msg)
    return url


@asynccontextmanager
async def aiohttp_ws_connector(url: str) -> AsyncIterator[WsSession]:
    """Default Socket-Mode connector backed by aiohttp.

    Yields:
        A :class:`WsSession` over the live aiohttp WebSocket.
    """
    # Deferred so importing the inbound package does not pull aiohttp into
    # every cold-import path (it is only needed at connect time).
    import aiohttp  # noqa: PLC0415

    async with (
        aiohttp.ClientSession() as http,
        http.ws_connect(url, heartbeat=30.0) as ws,
    ):
        yield _AiohttpWsSession(ws)


class _AiohttpWsSession:
    """Adapt an aiohttp WebSocket to :class:`WsSession`."""

    def __init__(self, ws: object) -> None:
        self._ws = ws

    def __aiter__(self) -> AsyncIterator[Mapping[str, object]]:
        return self._frames()

    async def _frames(self) -> AsyncIterator[Mapping[str, object]]:
        import aiohttp  # noqa: PLC0415

        assert isinstance(self._ws, aiohttp.ClientWebSocketResponse)  # noqa: S101
        async for msg in self._ws:
            if msg.type is aiohttp.WSMsgType.TEXT:
                decoded = json.loads(msg.data)
                if isinstance(decoded, Mapping):
                    yield decoded

    async def ack(self, envelope_id: str) -> None:
        import aiohttp  # noqa: PLC0415

        assert isinstance(self._ws, aiohttp.ClientWebSocketResponse)  # noqa: S101
        await self._ws.send_json({"envelope_id": envelope_id})


__all__ = [
    "EventHandler",
    "SlackSocketModeClient",
    "WsConnector",
    "WsSession",
    "aiohttp_ws_connector",
]
