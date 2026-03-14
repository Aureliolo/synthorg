"""Property-based tests for Message model roundtrips and alias handling."""

from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from synthorg.communication.enums import (
    AttachmentType,
    MessagePriority,
    MessageType,
)
from synthorg.communication.message import Attachment, Message, MessageMetadata

pytestmark = pytest.mark.unit

_not_blank = st.text(min_size=1, max_size=30).filter(lambda s: s.strip())
_message_types = st.sampled_from(MessageType)
_priorities = st.sampled_from(MessagePriority)
_attachment_types = st.sampled_from(AttachmentType)

_aware_datetimes = st.datetimes(
    min_value=datetime(2000, 1, 1),  # noqa: DTZ001 — bounds only; timezones= makes outputs UTC-aware
    max_value=datetime(2100, 1, 1),  # noqa: DTZ001 — bounds only; timezones= makes outputs UTC-aware
    timezones=st.just(UTC),
)

_message_kwargs_st = st.fixed_dictionaries(
    {
        "sender": _not_blank,
        "to": _not_blank,
        "msg_type": _message_types,
        "priority": _priorities,
        "channel": _not_blank,
        "content": _not_blank,
        "ts": _aware_datetimes,
    }
)


def _kwargs_to_message_dict(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": kwargs["sender"],
        "to": kwargs["to"],
        "type": kwargs["msg_type"],
        "priority": kwargs["priority"],
        "channel": kwargs["channel"],
        "content": kwargs["content"],
        "timestamp": kwargs["ts"],
    }


def _make_default_message_kwargs() -> dict[str, Any]:
    return {
        "from": "agent-sender",
        "to": "agent-receiver",
        "type": MessageType.TASK_UPDATE,
        "priority": MessagePriority.NORMAL,
        "channel": "general",
        "content": "Hello",
        "timestamp": datetime.now(UTC),
    }


class TestMessageRoundtripProperties:
    @given(data=_message_kwargs_st)
    @settings(max_examples=100)
    def test_model_dump_validate_roundtrip(self, data: dict[str, Any]) -> None:
        msg = Message(**_kwargs_to_message_dict(data))
        dumped = msg.model_dump(by_alias=True)
        restored = Message.model_validate(dumped)
        assert restored == msg

    @given(data=_message_kwargs_st)
    @settings(max_examples=50)
    def test_roundtrip_preserves_sender_alias(self, data: dict[str, Any]) -> None:
        msg = Message(**_kwargs_to_message_dict(data))
        sender = data["sender"]

        # Dump with alias -> "from" key
        dumped = msg.model_dump(by_alias=True)
        assert "from" in dumped
        assert dumped["from"] == sender

        # Dump without alias -> "sender" key
        dumped_no_alias = msg.model_dump()
        assert "sender" in dumped_no_alias
        assert dumped_no_alias["sender"] == sender


class TestFromAliasProperties:
    @given(sender=_not_blank)
    @settings(max_examples=50)
    def test_from_alias_works(self, sender: str) -> None:
        kwargs = _make_default_message_kwargs()
        kwargs["from"] = sender
        msg = Message(**kwargs)
        assert msg.sender == sender

    @given(sender=_not_blank)
    @settings(max_examples=50)
    def test_populate_by_name_works(self, sender: str) -> None:
        kwargs = _make_default_message_kwargs()
        del kwargs["from"]
        kwargs["sender"] = sender
        msg = Message(**kwargs)
        assert msg.sender == sender


class TestAttachmentRoundtripProperties:
    @given(
        att_type=_attachment_types,
        ref=_not_blank,
    )
    @settings(max_examples=50)
    def test_attachment_roundtrip(
        self,
        att_type: AttachmentType,
        ref: str,
    ) -> None:
        att = Attachment(type=att_type, ref=ref)
        dumped = att.model_dump()
        restored = Attachment.model_validate(dumped)
        assert restored == att


class TestMetadataRoundtripProperties:
    @given(
        task_id=st.one_of(st.none(), _not_blank),
        project_id=st.one_of(st.none(), _not_blank),
        tokens=st.one_of(st.none(), st.integers(min_value=0, max_value=10000)),
        cost=st.one_of(
            st.none(),
            st.floats(min_value=0.0, max_value=100.0, allow_nan=False),
        ),
    )
    @settings(max_examples=100)
    def test_metadata_roundtrip(
        self,
        task_id: str | None,
        project_id: str | None,
        tokens: int | None,
        cost: float | None,
    ) -> None:
        meta = MessageMetadata(
            task_id=task_id,
            project_id=project_id,
            tokens_used=tokens,
            cost_usd=cost,
        )
        dumped = meta.model_dump()
        restored = MessageMetadata.model_validate(dumped)
        assert restored == meta
