"""Pure WebSocket protocol helpers.

Extracted from ``ws.py`` to keep the main handler focused on the
connection lifecycle. Everything here is a stateless function over
already-validated inputs -- no sockets, no async side effects beyond
emitting a log line. This lets ``ws.py`` import individual hooks
(``_parse_event_payload``, ``_handle_message``, ...) and stay below the
800-line module ceiling the project enforces.
"""

import json

from pydantic import ValidationError

from synthorg.api.channels import (
    ALL_CHANNELS,
    BUDGET_CHANNELS,
    extract_user_id,
    is_user_channel,
    user_channel,
)
from synthorg.api.ws_control_models import (
    WS_CONTROL_MESSAGE_ADAPTER,
    WsPingMessage,
    WsSubscribeMessage,
    WsUnsubscribeMessage,
)
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.auth.roles import HumanRole
from synthorg.core.boundary import parse_typed
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_WS_INVALID_MESSAGE,
    API_WS_PING,
    API_WS_SUBSCRIBE,
    API_WS_UNKNOWN_ACTION,
    API_WS_UNSUBSCRIBE,
    API_WS_USER_CHANNEL_DENIED,
)

logger = get_logger(__name__)

_ALL_CHANNELS_SET: frozenset[str] = frozenset(ALL_CHANNELS)
_MAX_FILTER_KEYS: int = 10
_MAX_FILTER_VALUE_LEN: int = 256
# Inbound (client -> server) control-message size cap. Subscribe/unsubscribe
# /auth/ping payloads max out around 3 KiB even at full filter limits, so 4
# KiB is a tight DoS guard with deliberate headroom. Keep this in sync with
# ``WS_MAX_INBOUND_BYTES_DEFAULT`` if/when added to client config.
_MAX_WS_MESSAGE_BYTES: int = 4096


def matches_filters(
    event: dict[str, object],
    channel: str,
    channel_filters: dict[str, str],
) -> bool:
    """Check whether the event payload matches the active channel filters.

    A filter key absent from the payload is a mismatch. ``payload.get``
    returns ``None`` for a missing key, so comparing directly against
    the filter value would incorrectly let clients widen subscriptions
    by sending ``{"task_id": null}`` as the filter: payloads without
    that key would match. Use explicit ``in``-checks instead so a
    missing payload key always fails the filter.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        logger.warning(
            API_WS_INVALID_MESSAGE,
            channel=channel,
            reason="payload_not_dict",
            payload_type=type(payload).__name__,
        )
        return False
    for key, expected in channel_filters.items():
        if key not in payload:
            return False
        if payload[key] != expected:
            return False
    return True


def channel_allowed(
    channel: str,
    conn_user: AuthenticatedUser,
) -> bool:
    """Check whether the connected user may receive this channel.

    Server-side access control:
    - User channels: only the owning user.
    - Budget channels: CEO or Manager only.
    - All others: any read-capable user.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    if is_user_channel(channel):
        return extract_user_id(channel) == conn_user.user_id
    if channel in BUDGET_CHANNELS:
        return conn_user.role in (HumanRole.CEO, HumanRole.MANAGER)
    return True


def parse_event_payload(event_data: bytes) -> dict[str, object] | None:
    """Decode the raw channel payload into a dict, logging+dropping on errors.

    Enforce UTF-8 explicitly before ``json.loads``: Python's JSON
    codec infers encoding from a BOM (UTF-16/UTF-32 bytes round-trip
    through ``json.loads`` without raising), but the downstream
    ``event_data.decode("utf-8")`` call in ``ws.py`` would then fail
    on the same bytes. Decoding here first catches both cases with a
    single ``UnicodeDecodeError``.

    Returns:
        The ``dict[str, object]`` value when present, ``None`` otherwise.
    """
    try:
        text = (
            event_data.decode("utf-8")
            if isinstance(event_data, (bytes, bytearray))
            else event_data
        )
        event = json.loads(text)
    except json.JSONDecodeError, UnicodeDecodeError:
        logger.warning(
            API_WS_INVALID_MESSAGE,
            data_preview=str(event_data)[:100],
            source="channels_backend",
        )
        return None
    except TypeError:
        logger.error(
            API_WS_INVALID_MESSAGE,
            data_type=type(event_data).__name__,
            reason="unexpected_type",
            source="channels_backend",
        )
        return None

    if not isinstance(event, dict):
        logger.warning(
            API_WS_INVALID_MESSAGE,
            data_preview=str(event_data)[:100],
            reason="not_a_dict",
        )
        return None
    return event


def _parse_ws_message(data: str) -> dict[str, object] | str:
    """Parse raw JSON from the client, returning a dict or an error string.

    Returns:
        The parsed ``dict[str, object]`` on success, or a JSON-encoded
        error string (e.g. ``'{"error": "..."}'``) when the payload is
        too large or not a valid JSON object.
    """
    encoded = data.encode()
    if len(encoded) > _MAX_WS_MESSAGE_BYTES:
        logger.warning(
            API_WS_INVALID_MESSAGE,
            reason="message_too_large",
            size=len(encoded),
        )
        return json.dumps({"error": "Message too large"})

    try:
        msg = json.loads(data)
    except json.JSONDecodeError:
        logger.warning(API_WS_INVALID_MESSAGE, data_preview=str(data)[:100])
        return json.dumps({"error": "Invalid JSON"})
    except TypeError:
        logger.error(
            API_WS_INVALID_MESSAGE,
            data_type=type(data).__name__,
            reason="unexpected_type",
        )
        return json.dumps({"error": "Invalid JSON"})

    if not isinstance(msg, dict):
        logger.warning(
            API_WS_INVALID_MESSAGE,
            reason="not_a_dict",
            message_type=type(msg).__name__,
        )
        return json.dumps({"error": "Expected JSON object"})

    return msg


def handle_message(
    data: str,
    subscribed: set[str],
    filters: dict[str, dict[str, str]],
    conn_user: AuthenticatedUser,
) -> str:
    """Parse, validate, and dispatch a single client message.

    The raw JSON is parsed, then routed through
    :func:`synthorg.core.boundary.parse_typed` against
    :data:`WS_CONTROL_MESSAGE_ADAPTER` so the typed variant
    (:class:`WsSubscribeMessage`, :class:`WsUnsubscribeMessage`,
    :class:`WsPingMessage`, :class:`WsAuthMessage`) drives dispatch.
    A frame that fails typed validation surfaces the canonical
    ``api.boundary.validation_failed`` event from ``parse_typed`` (with
    the full error locations); the pre-parse not-a-dict / not-JSON path
    in :func:`_parse_ws_message` is the only remaining emitter of the
    ws-specific ``api.ws.invalid_message`` event.

    Returns:
        Resulting string.
    """
    parsed = _parse_ws_message(data)
    if isinstance(parsed, str):
        return parsed

    try:
        message = parse_typed("ws.control", parsed, WS_CONTROL_MESSAGE_ADAPTER)
    except ValidationError:
        # parse_typed already emitted api.boundary.validation_failed with
        # the full error locations; no second ws-specific mirror.
        return json.dumps({"error": "Invalid control message"})

    if isinstance(message, WsPingMessage):
        logger.debug(API_WS_PING)
        return json.dumps({"action": "pong"})

    if isinstance(message, WsSubscribeMessage):
        return _handle_subscribe_typed(
            message,
            subscribed,
            filters,
            conn_user,
        )

    if isinstance(message, WsUnsubscribeMessage):
        return _handle_unsubscribe(list(message.channels), subscribed, filters)

    # WsAuthMessage arriving on an already-authenticated socket: the
    # post-auth control plane does not accept additional auth frames,
    # so reject without leaking that the auth handshake exists at all.
    logger.warning(API_WS_UNKNOWN_ACTION, action=message.action[:64])
    return json.dumps({"error": "Unknown action"})


def _handle_subscribe_typed(
    message: WsSubscribeMessage,
    subscribed: set[str],
    filters: dict[str, dict[str, str]],
    conn_user: AuthenticatedUser,
) -> str:
    """Process a typed subscribe message.

    Filter semantics:
        ``None``  -- filters key absent, leave existing filters unchanged.
        ``{}``    -- explicitly clear filters for the subscribed channels.
        ``{...}`` -- set new filters for the subscribed channels.

    Returns:
        Resulting string.
    """
    client_filters = message.filters
    if client_filters is not None and (
        len(client_filters) > _MAX_FILTER_KEYS
        or any(len(v) > _MAX_FILTER_VALUE_LEN for v in client_filters.values())
    ):
        logger.warning(
            API_WS_INVALID_MESSAGE,
            reason="filters_bounds_exceeded",
            filter_count=len(client_filters),
            max_keys=_MAX_FILTER_KEYS,
            max_value_len=_MAX_FILTER_VALUE_LEN,
        )
        return json.dumps({"error": "Filter bounds exceeded"})

    # Accept known channels the user is authorized to receive.
    own_user_ch = user_channel(conn_user.user_id)
    valid: list[str] = []
    for c in message.channels:
        if c == own_user_ch or (
            c in _ALL_CHANNELS_SET and channel_allowed(c, conn_user)
        ):
            valid.append(c)
        elif is_user_channel(c):
            logger.warning(
                API_WS_USER_CHANNEL_DENIED,
                user_id=conn_user.user_id,
                channel="user:<redacted>",
            )
            # Silently drop -- don't expose other user IDs.
    subscribed.update(valid)
    if client_filters is not None:
        for c in valid:
            if client_filters:
                filters[c] = dict(client_filters)
            else:
                filters.pop(c, None)
    # Subscribe is a state transition ("channel added to active set"),
    # so log at INFO per project logging rules so operators can see
    # per-connection subscription churn in normal dashboards.
    logger.info(
        API_WS_SUBSCRIBE,
        channels=valid,
        active=sorted(subscribed),
    )
    return json.dumps({"action": "subscribed", "channels": sorted(subscribed)})


def _handle_unsubscribe(
    channels: list[str],
    subscribed: set[str],
    filters: dict[str, dict[str, str]],
) -> str:
    """Process an unsubscribe action.

    Returns:
        Resulting string.
    """
    subscribed -= set(channels)
    for c in channels:
        filters.pop(c, None)
    # Mirror subscribe: unsubscribe is also a state transition.
    logger.info(
        API_WS_UNSUBSCRIBE,
        channels=channels,
        active=sorted(subscribed),
    )
    return json.dumps({"action": "unsubscribed", "channels": sorted(subscribed)})
