"""Tests for the vendor-neutral inbound chat event model invariants."""

import pytest
from pydantic import ValidationError

from synthorg.integrations.chat_api.inbound.models import (
    InboundChatEvent,
    InboundEventKind,
)

pytestmark = pytest.mark.unit


class TestInboundChatEvent:
    def test_valid_text_event(self) -> None:
        event = InboundChatEvent(
            kind=InboundEventKind.MENTION, channel="C1", user="U1", text="hi"
        )
        assert event.reaction == ""

    def test_valid_reaction_event(self) -> None:
        event = InboundChatEvent(
            kind=InboundEventKind.REACTION,
            channel="C1",
            user="U1",
            reaction="white_check_mark",
        )
        assert event.reaction == "white_check_mark"

    @pytest.mark.parametrize("channel", ["", "   "], ids=["empty", "whitespace"])
    def test_blank_channel_rejected(self, channel: str) -> None:
        with pytest.raises(ValidationError):
            InboundChatEvent(kind=InboundEventKind.MESSAGE, channel=channel, user="U1")

    @pytest.mark.parametrize("user", ["", "   "], ids=["empty", "whitespace"])
    def test_blank_user_rejected(self, user: str) -> None:
        # Whitespace-only is truthy, so a plain emptiness check would let an
        # unattributable decider through.
        with pytest.raises(ValidationError):
            InboundChatEvent(kind=InboundEventKind.MESSAGE, channel="C1", user=user)

    def test_reaction_on_non_reaction_kind_rejected(self) -> None:
        with pytest.raises(ValidationError, match="reaction must be set iff"):
            InboundChatEvent(
                kind=InboundEventKind.MENTION,
                channel="C1",
                user="U1",
                reaction="thumbsup",
            )

    def test_reaction_kind_without_reaction_rejected(self) -> None:
        with pytest.raises(ValidationError, match="reaction must be set iff"):
            InboundChatEvent(
                kind=InboundEventKind.REACTION, channel="C1", user="U1", reaction=""
            )
