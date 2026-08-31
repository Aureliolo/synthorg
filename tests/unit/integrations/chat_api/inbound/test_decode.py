"""Tests for pure Socket-Mode frame decoding."""

import pytest

from synthorg.integrations.chat_api.inbound.decode import (
    DecodedFrame,
    DecodeDropReason,
    decode_frame,
)
from synthorg.integrations.chat_api.inbound.models import (
    InboundChatEvent,
    InboundEventKind,
)

pytestmark = pytest.mark.unit


def _events_api(
    event: dict[str, object], *, envelope_id: str = "env-1"
) -> dict[str, object]:
    return {
        "type": "events_api",
        "envelope_id": envelope_id,
        "payload": {"event": event},
    }


class TestControlFrames:
    def test_hello_is_inert(self) -> None:
        decoded = decode_frame({"type": "hello"})
        assert decoded.envelope_id == ""
        assert decoded.event is None
        assert decoded.disconnect is False
        assert decoded.drop_reason is None

    def test_disconnect_signals_reconnect(self) -> None:
        decoded = decode_frame({"type": "disconnect", "reason": "refresh"})
        assert decoded.disconnect is True
        assert decoded.drop_reason is None

    def test_interactive_frame_is_acked_only(self) -> None:
        decoded = decode_frame({"type": "interactive", "envelope_id": "env-9"})
        assert decoded.envelope_id == "env-9"
        assert decoded.event is None
        assert decoded.drop_reason is None


class TestEventDecoding:
    def test_app_mention_maps_to_mention(self) -> None:
        decoded = decode_frame(
            _events_api(
                {
                    "type": "app_mention",
                    "user": "U1",
                    "text": "<@bot> please proceed",
                    "ts": "100.1",
                    "thread_ts": "99.0",
                    "channel": "C1",
                }
            )
        )
        assert decoded.envelope_id == "env-1"
        assert decoded.event is not None
        assert decoded.event.kind is InboundEventKind.MENTION
        assert decoded.event.channel == "C1"
        assert decoded.event.thread_ts == "99.0"
        assert decoded.event.text == "<@bot> please proceed"

    def test_top_level_message_roots_its_own_thread(self) -> None:
        decoded = decode_frame(
            _events_api(
                {
                    "type": "message",
                    "user": "U1",
                    "text": "hi",
                    "ts": "100.1",
                    "channel": "C1",
                }
            )
        )
        assert decoded.event is not None
        assert decoded.event.kind is InboundEventKind.MESSAGE
        # No thread_ts -> the message's own ts is the correlation root.
        assert decoded.event.thread_ts == "100.1"

    def test_im_message_maps_to_direct_message(self) -> None:
        decoded = decode_frame(
            _events_api(
                {
                    "type": "message",
                    "user": "U1",
                    "text": "dm",
                    "ts": "100.1",
                    "channel": "D1",
                    "channel_type": "im",
                }
            )
        )
        assert decoded.event is not None
        assert decoded.event.kind is InboundEventKind.DIRECT_MESSAGE

    def test_reaction_uses_item_channel_and_ts(self) -> None:
        decoded = decode_frame(
            _events_api(
                {
                    "type": "reaction_added",
                    "user": "U1",
                    "reaction": "white_check_mark",
                    "item": {"channel": "C1", "ts": "88.0"},
                }
            )
        )
        assert decoded.event is not None
        assert decoded.event.kind is InboundEventKind.REACTION
        assert decoded.event.channel == "C1"
        assert decoded.event.thread_ts == "88.0"
        assert decoded.event.reaction == "white_check_mark"

    def test_bot_message_is_ignored(self) -> None:
        decoded = decode_frame(
            _events_api(
                {
                    "type": "message",
                    "bot_id": "B1",
                    "text": "echo",
                    "ts": "1.0",
                    "channel": "C1",
                }
            )
        )
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.BOT_AUTHORED
        # Still acked so Slack stops re-delivering.
        assert decoded.envelope_id == "env-1"

    def test_message_subtype_is_ignored(self) -> None:
        decoded = decode_frame(
            _events_api(
                {
                    "type": "message",
                    "subtype": "message_changed",
                    "user": "U1",
                    "text": "edited",
                    "ts": "1.0",
                    "channel": "C1",
                }
            )
        )
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.MESSAGE_SUBTYPE

    def test_message_without_author_is_ignored(self) -> None:
        decoded = decode_frame(
            _events_api({"type": "message", "text": "x", "ts": "1.0", "channel": "C1"})
        )
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.MISSING_ATTRIBUTION

    def test_message_without_channel_is_ignored(self) -> None:
        decoded = decode_frame(
            _events_api({"type": "message", "user": "U1", "text": "x", "ts": "1.0"})
        )
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.MISSING_ATTRIBUTION

    def test_unroutable_event_type_is_ignored(self) -> None:
        decoded = decode_frame(_events_api({"type": "team_join", "user": "U1"}))
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.UNROUTABLE_TYPE

    def test_reaction_without_item_is_ignored(self) -> None:
        decoded = decode_frame(
            _events_api({"type": "reaction_added", "user": "U1", "reaction": "eyes"})
        )
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.MALFORMED_REACTION

    def test_reaction_without_author_is_ignored(self) -> None:
        decoded = decode_frame(
            _events_api(
                {
                    "type": "reaction_added",
                    "reaction": "eyes",
                    "item": {"channel": "C1", "ts": "1.0"},
                }
            )
        )
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.MALFORMED_REACTION

    def test_reaction_without_shortcode_is_ignored(self) -> None:
        decoded = decode_frame(
            _events_api(
                {
                    "type": "reaction_added",
                    "user": "U1",
                    "item": {"channel": "C1", "ts": "1.0"},
                }
            )
        )
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.MALFORMED_REACTION

    def test_event_without_envelope_id_is_dropped(self) -> None:
        """An unackable event frame decodes inert instead of raising.

        ``DecodedFrame`` refuses an event with no envelope to ack, and a
        malformed frame from the socket must not take the consumer loop
        down with it. This is the branch that drops an already-decoded,
        routable human reply purely for lack of an envelope id.
        """
        decoded = decode_frame(
            {
                "type": "events_api",
                "payload": {
                    "event": {
                        "type": "app_mention",
                        "user": "U1",
                        "text": "hi",
                        "ts": "1.0",
                        "channel": "C1",
                    }
                },
            }
        )
        assert decoded.event is None
        assert decoded.envelope_id == ""
        assert decoded.drop_reason is DecodeDropReason.NO_ENVELOPE_ID

    def test_malformed_payload_is_ignored_but_acked(self) -> None:
        decoded = decode_frame(
            {"type": "events_api", "envelope_id": "e2", "payload": "nope"}
        )
        assert decoded.envelope_id == "e2"
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.MALFORMED_PAYLOAD

    def test_malformed_event_is_ignored_but_acked(self) -> None:
        decoded = decode_frame(
            {
                "type": "events_api",
                "envelope_id": "e3",
                "payload": {"event": "nope"},
            }
        )
        assert decoded.envelope_id == "e3"
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.MALFORMED_EVENT

    def test_validation_failure_is_ignored_but_acked(self) -> None:
        decoded = decode_frame(
            _events_api({"type": "message", "user": 123, "channel": "C1"})
        )
        assert decoded.event is None
        assert decoded.drop_reason is DecodeDropReason.VALIDATION_FAILED


class TestDecodedFrameShape:
    def test_disconnect_with_event_rejected(self) -> None:
        event = InboundChatEvent(
            kind=InboundEventKind.MENTION, channel="C1", user="U1", text="hi"
        )
        with pytest.raises(ValueError, match="disconnect frame cannot carry"):
            DecodedFrame(envelope_id="e1", event=event, disconnect=True)

    def test_event_without_envelope_rejected(self) -> None:
        event = InboundChatEvent(
            kind=InboundEventKind.MENTION, channel="C1", user="U1", text="hi"
        )
        with pytest.raises(ValueError, match="requires an envelope id"):
            DecodedFrame(event=event)

    def test_disconnect_with_drop_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match="disconnect frame cannot carry a drop"):
            DecodedFrame(
                disconnect=True, drop_reason=DecodeDropReason.MALFORMED_PAYLOAD
            )
