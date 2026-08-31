"""Pure Socket-Mode frame decoding (no I/O).

Slack Socket-Mode multiplexes several frame types over one WebSocket:
``hello`` (connection ack), ``disconnect`` (server asks us to reconnect),
and ``events_api`` / ``interactive`` / ``slash_commands`` envelopes that
each carry an ``envelope_id`` we must acknowledge. This module turns a raw
frame mapping into a :class:`DecodedFrame` telling the transport what to
ack and the router what (if anything) to route, keeping every branch unit
testable without a socket.

Every branch that drops a would-be event carries a :class:`DecodeDropReason`
rather than collapsing silently into the same shape as a ``hello`` keepalive.
This module stays pure: it names the reason, the caller (``socket_mode.py``)
is the one that logs it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError

from synthorg.integrations.chat_api.inbound.models import (
    InboundChatEvent,
    InboundEventKind,
)

# Socket-Mode frame ``type`` values. HELLO and DISCONNECT are public: they
# are the two frame types that carry no Slack-originated activity (a
# keepalive ack and a server-initiated reconnect signal), so the receive
# loop needs them to keep its own "an event arrived" volume signal from
# counting protocol chatter as an event.
FRAME_HELLO: Final[str] = "hello"
FRAME_DISCONNECT: Final[str] = "disconnect"
_FRAME_EVENTS_API: Final[str] = "events_api"

# Slack inner-event ``type`` values we route.
_EVENT_APP_MENTION: Final[str] = "app_mention"
_EVENT_MESSAGE: Final[str] = "message"
_EVENT_REACTION_ADDED: Final[str] = "reaction_added"

# Slack's channel_type for a direct message.
_DIRECT_CHANNEL_TYPE: Final[str] = "im"


class DecodeDropReason(StrEnum):
    """Why a would-be event was dropped instead of routed.

    One member per silent branch in this module, so the receive loop can
    log a volume and reason signal instead of the frame vanishing with no
    trace at any log level.
    """

    NO_ENVELOPE_ID = "no_envelope_id"
    """A fully-decoded, routable event arrived on a frame with no
    (or non-string) ``envelope_id``. The sharpest case: this can drop a
    successfully-decoded human reply, reaction included."""

    MALFORMED_PAYLOAD = "malformed_payload"
    MALFORMED_EVENT = "malformed_event"
    VALIDATION_FAILED = "validation_failed"
    BOT_AUTHORED = "bot_authored"
    MESSAGE_SUBTYPE = "message_subtype"
    UNROUTABLE_TYPE = "unroutable_type"
    MISSING_ATTRIBUTION = "missing_attribution"
    MALFORMED_REACTION = "malformed_reaction"


#: Reasons any ordinary channel member (or an attacker with nothing more
#: than channel access) triggers just by using Slack normally: a bot's own
#: message echoing back, an edit/join/etc. subtype, or an event type this
#: integration has no handler for. None of these represents a lost or
#: malformed event, unlike every other reason, so flooding them at the same
#: severity as a lost human reply would let routine chat traffic bury the
#: one drop reason that actually matters. Logged at ``info``, not
#: ``warning`` (see ``socket_mode.py``).
ROUTINE_DROP_REASONS: Final[frozenset[DecodeDropReason]] = frozenset(
    {
        DecodeDropReason.BOT_AUTHORED,
        DecodeDropReason.MESSAGE_SUBTYPE,
        DecodeDropReason.UNROUTABLE_TYPE,
    }
)


@dataclass(frozen=True)
class DecodedFrame:
    """The outcome of decoding one Socket-Mode frame.

    Exactly one legal shape holds: nothing (hello), ``disconnect``, an
    ack-only ``envelope_id``, or an ``envelope_id`` with an ``event``. The
    post-init assertion rejects the self-contradicting combinations so a
    future second producer cannot construct one silently.

    Attributes:
        envelope_id: Acknowledge this envelope when non-empty (every
            typed envelope must be acked promptly or Slack re-sends it).
        event: The routable human event, or ``None`` for a frame that
            needs no routing (hello / ack-only / bot / ignored subtype).
        disconnect: The server asked us to reconnect.
        drop_reason: Set when a would-be event was dropped rather than
            simply absent (a ``hello`` or an acked-but-unrouted
            interactive/slash_commands frame carries neither an event nor
            a drop reason; both are expected, not failures).
    """

    envelope_id: str = ""
    event: InboundChatEvent | None = None
    disconnect: bool = False
    drop_reason: DecodeDropReason | None = None

    def __post_init__(self) -> None:
        """Reject a self-contradicting frame shape.

        Raises:
            ValueError: When ``disconnect`` carries an event or a drop
                reason, an event arrives without its envelope id, or a
                drop reason accompanies an actual event.
        """
        if self.disconnect and self.event is not None:
            msg = "a disconnect frame cannot carry a routable event"
            raise ValueError(msg)
        if self.disconnect and self.drop_reason is not None:
            msg = "a disconnect frame cannot carry a drop reason"
            raise ValueError(msg)
        if self.event is not None and not self.envelope_id:
            msg = "a routable event requires an envelope id to acknowledge"
            raise ValueError(msg)
        if self.event is not None and self.drop_reason is not None:
            msg = "a routed event cannot also carry a drop reason"
            raise ValueError(msg)


class _SlInner(BaseModel):  # lint-allow: frozen-extra-forbid -- slack extras
    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="ignore")


class _SlItem(_SlInner):
    channel: str = ""
    ts: str = ""


class _SlEvent(_SlInner):
    type: str = ""
    user: str = ""
    text: str = ""
    ts: str = ""
    thread_ts: str = ""
    channel: str = ""
    channel_type: str = ""
    subtype: str = ""
    bot_id: str = ""
    reaction: str = ""
    item: _SlItem | None = None


def decode_frame(frame: Mapping[str, object]) -> DecodedFrame:
    """Decode one Socket-Mode frame.

    Returns:
        A :class:`DecodedFrame`; unknown or non-routable frames yield an
        empty envelope id and no event so the caller simply drops them,
        naming why via ``drop_reason`` where an event was actually lost.
    """
    frame_type = frame.get("type")
    if frame_type == FRAME_DISCONNECT:
        return DecodedFrame(disconnect=True)
    if frame_type == FRAME_HELLO:
        return DecodedFrame()
    envelope_id = frame.get("envelope_id")
    envelope = envelope_id if isinstance(envelope_id, str) else ""
    if frame_type != _FRAME_EVENTS_API:
        # interactive / slash_commands: ack so Slack stops re-sending, but
        # we do not act on them. Not a drop: nothing was decoded to lose.
        return DecodedFrame(envelope_id=envelope)
    event, reason = _event_from(frame)
    if event is not None and not envelope:
        # Nothing to acknowledge the event with. Drop it like every other
        # malformed-frame branch here rather than raising and forcing a
        # socket reconnect over one bad vendor frame.
        return DecodedFrame(drop_reason=DecodeDropReason.NO_ENVELOPE_ID)
    return DecodedFrame(envelope_id=envelope, event=event, drop_reason=reason)


def _event_from(
    frame: Mapping[str, object],
) -> tuple[InboundChatEvent | None, DecodeDropReason | None]:
    """Extract a routable event from an ``events_api`` frame.

    Returns:
        The mapped event, or ``None`` with the reason for a malformed
        payload or a bot-authored / non-routable inner event.
    """
    payload = frame.get("payload")
    if not isinstance(payload, Mapping):
        return None, DecodeDropReason.MALFORMED_PAYLOAD
    raw_event = payload.get("event")
    if not isinstance(raw_event, Mapping):
        return None, DecodeDropReason.MALFORMED_EVENT
    try:
        event = _SlEvent.model_validate(raw_event)
    except ValidationError:
        return None, DecodeDropReason.VALIDATION_FAILED
    # A message Slack itself posted (our own bot echoes, join notices,
    # edits) must never resume a task: ignore bot authors and subtypes.
    if event.bot_id:
        return None, DecodeDropReason.BOT_AUTHORED
    return _map_event(event)


def _map_event(
    event: _SlEvent,
) -> tuple[InboundChatEvent | None, DecodeDropReason | None]:
    """Map a validated Slack inner event onto the vendor-neutral model.

    Returns:
        The mapped event, or ``None`` with the reason for an event type /
        subtype that does not resume a task.
    """
    if event.type == _EVENT_APP_MENTION:
        return _text_event(event, InboundEventKind.MENTION)
    if event.type == _EVENT_MESSAGE:
        # A message with a subtype is an edit/join/system post, not a
        # human reply; only a plain, human-authored message resumes a task.
        if event.subtype:
            return None, DecodeDropReason.MESSAGE_SUBTYPE
        kind = (
            InboundEventKind.DIRECT_MESSAGE
            if event.channel_type == _DIRECT_CHANNEL_TYPE
            else InboundEventKind.MESSAGE
        )
        return _text_event(event, kind)
    if event.type == _EVENT_REACTION_ADDED:
        return _reaction_event(event)
    return None, DecodeDropReason.UNROUTABLE_TYPE


def _text_event(
    event: _SlEvent, kind: InboundEventKind
) -> tuple[InboundChatEvent | None, DecodeDropReason | None]:
    """Build a text-reply event.

    Returns:
        The event, or ``None`` with the reason when the message has no
        author or channel (an unroutable, unattributable frame).
    """
    if not event.user or not event.channel:
        return None, DecodeDropReason.MISSING_ATTRIBUTION
    return (
        InboundChatEvent(
            kind=kind,
            channel=event.channel,
            user=event.user,
            text=event.text,
            ts=event.ts,
            # A top-level message is its own thread root for correlation.
            thread_ts=event.thread_ts or event.ts,
        ),
        None,
    )


def _reaction_event(
    event: _SlEvent,
) -> tuple[InboundChatEvent | None, DecodeDropReason | None]:
    """Build a reaction event.

    Returns:
        The event, or ``None`` with the reason when the reaction lacks an
        item, author, channel, or shortcode (nothing to correlate or
        decide on).
    """
    if (
        event.item is None
        or not event.user
        or not event.item.channel
        or not event.reaction
    ):
        return None, DecodeDropReason.MALFORMED_REACTION
    return (
        InboundChatEvent(
            kind=InboundEventKind.REACTION,
            channel=event.item.channel,
            user=event.user,
            ts=event.item.ts,
            thread_ts=event.item.ts,
            reaction=event.reaction,
        ),
        None,
    )


__all__ = [
    "FRAME_DISCONNECT",
    "FRAME_HELLO",
    "ROUTINE_DROP_REASONS",
    "DecodeDropReason",
    "DecodedFrame",
    "decode_frame",
]
