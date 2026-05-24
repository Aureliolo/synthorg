# mypy: disable-error-code="explicit-any"
"""Property-based tests for Message model roundtrips and alias handling."""

from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.message import (
    DataPart,
    FilePart,
    Message,
    MessageMetadata,
    TextPart,
    UriPart,
)

pytestmark = pytest.mark.unit

_not_blank = st.text(min_size=1, max_size=30).filter(lambda s: s.strip())
_message_types = st.sampled_from(MessageType)
_priorities = st.sampled_from(MessagePriority)

_aware_datetimes = st.datetimes(
    min_value=datetime(2000, 1, 1),  # noqa: DTZ001 -- bounds only; timezones= makes outputs UTC-aware
    max_value=datetime(2100, 1, 1),  # noqa: DTZ001 -- bounds only; timezones= makes outputs UTC-aware
    timezones=st.just(UTC),
)

# Part strategies
_text_parts = st.builds(TextPart, text=_not_blank)

_json_leaves = st.one_of(
    st.text(min_size=1, max_size=20),
    st.integers(min_value=-1000, max_value=1000),
    st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6),
    st.booleans(),
    st.none(),
)

_nested_values = st.recursive(
    _json_leaves,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(_not_blank, children, max_size=3),
    ),
    max_leaves=10,
)

_data_parts = st.builds(
    DataPart,
    data=st.dictionaries(_not_blank, _nested_values, min_size=1, max_size=4),
)

_file_parts = st.builds(
    FilePart,
    uri=_not_blank,
    mime_type=st.one_of(st.none(), st.just("application/json")),
)

_uri_parts = st.builds(UriPart, uri=_not_blank)

_parts = st.one_of(_text_parts, _data_parts, _file_parts, _uri_parts)

_message_kwargs_st = st.fixed_dictionaries(
    {
        "sender": _not_blank,
        "to": _not_blank,
        "msg_type": _message_types,
        "priority": _priorities,
        "channel": _not_blank,
        "parts": st.tuples(_text_parts, st.lists(_parts, max_size=2)).map(
            lambda t: (t[0], *t[1])
        ),
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
        "parts": kwargs["parts"],
        "timestamp": kwargs["ts"],
    }


def _make_default_message_kwargs() -> dict[str, Any]:
    return {
        "from": "agent-sender",
        "to": "agent-receiver",
        "type": MessageType.TASK_UPDATE,
        "priority": MessagePriority.NORMAL,
        "channel": "general",
        "parts": (TextPart(text="Hello"),),
        "timestamp": datetime.now(UTC),
    }


class TestPartRoundtripProperties:
    @given(part=_text_parts)
    def test_text_part_roundtrip(self, part: TextPart) -> None:
        dumped = part.model_dump()
        restored = TextPart.model_validate(dumped)
        assert restored == part

    @given(part=_data_parts)
    def test_data_part_roundtrip(self, part: DataPart) -> None:
        dumped = part.model_dump()
        restored = DataPart.model_validate(dumped)
        assert restored == part

    @given(part=_file_parts)
    def test_file_part_roundtrip(self, part: FilePart) -> None:
        dumped = part.model_dump()
        restored = FilePart.model_validate(dumped)
        assert restored == part

    @given(part=_uri_parts)
    def test_uri_part_roundtrip(self, part: UriPart) -> None:
        dumped = part.model_dump()
        restored = UriPart.model_validate(dumped)
        assert restored == part


class TestMessageRoundtripProperties:
    @given(data=_message_kwargs_st)
    def test_model_dump_validate_roundtrip(self, data: dict[str, Any]) -> None:
        msg = Message(**_kwargs_to_message_dict(data))
        dumped = msg.model_dump(by_alias=True)
        restored = Message.model_validate(dumped)
        assert restored == msg

    @given(data=_message_kwargs_st)
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
    def test_from_alias_works(self, sender: str) -> None:
        kwargs = _make_default_message_kwargs()
        kwargs["from"] = sender
        msg = Message(**kwargs)
        assert msg.sender == sender

    @given(sender=_not_blank)
    def test_populate_by_name_works(self, sender: str) -> None:
        kwargs = _make_default_message_kwargs()
        del kwargs["from"]
        kwargs["sender"] = sender
        msg = Message(**kwargs)
        assert msg.sender == sender


class TestTextPropertyProperties:
    @given(text=_not_blank)
    def test_text_property_returns_first_text_part(self, text: str) -> None:
        msg = Message(
            **{
                **_make_default_message_kwargs(),
                "parts": (TextPart(text=text),),
            }
        )
        assert msg.text == text

    @given(text=_not_blank)
    def test_text_property_returns_first_text_part_from_mixed(self, text: str) -> None:
        msg = Message(
            **{
                **_make_default_message_kwargs(),
                "parts": (
                    TextPart(text=text),
                    DataPart(data={"key": "value"}),  # type: ignore[arg-type]
                    UriPart(uri="https://example.com"),
                ),
            }
        )
        assert msg.text == text

    @given(data=st.fixed_dictionaries({"key": _not_blank}))
    def test_text_property_returns_empty_when_no_text_part(
        self, data: dict[str, str]
    ) -> None:
        msg = Message(
            **{
                **_make_default_message_kwargs(),
                "parts": (
                    DataPart(data=data),  # type: ignore[arg-type]
                    UriPart(uri="https://example.com"),
                ),
            }
        )
        assert msg.text == ""

    def test_text_property_finds_text_part_not_first(self) -> None:
        """Message.text returns the first TextPart even if it is not index 0."""
        msg = Message(
            **{
                **_make_default_message_kwargs(),
                "parts": (
                    DataPart(data={"key": "value"}),  # type: ignore[arg-type]
                    TextPart(text="the actual text"),
                    UriPart(uri="https://example.com"),
                ),
            }
        )
        assert msg.text == "the actual text"


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
            cost=cost,
        )
        dumped = meta.model_dump()
        restored = MessageMetadata.model_validate(dumped)
        assert restored == meta
