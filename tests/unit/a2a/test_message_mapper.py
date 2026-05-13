"""Tests for Message <-> A2AMessage bidirectional mapping."""

from datetime import UTC, datetime

import pytest

from synthorg.a2a.message_mapper import from_a2a, to_a2a
from synthorg.a2a.models import (
    A2ADataPart,
    A2AFilePart,
    A2AMessage,
    A2AMessageRole,
    A2AMetadataKey,
    A2ATextPart,
)
from synthorg.communication.enums import MessageType
from synthorg.communication.message import (
    DataPart,
    FilePart,
    Message,
    TextPart,
    UriPart,
)


def _make_message(
    *,
    msg_type: MessageType = MessageType.TASK_UPDATE,
    parts: tuple[TextPart | DataPart | FilePart | UriPart, ...] | None = None,
) -> Message:
    """Create a minimal internal Message for testing."""
    if parts is None:
        parts = (TextPart(text="hello"),)
    return Message(
        timestamp=datetime.now(UTC),
        sender="agent-a",
        to="agent-b",
        type=msg_type,
        channel="test-channel",
        parts=parts,
    )


class TestToA2A:
    """Internal Message -> A2AMessage."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("part", "expected_cls", "attr", "expected_val"),
        [
            (TextPart(text="hello"), A2ATextPart, "text", "hello"),
            (
                DataPart(data={"key": "value"}),  # type: ignore[arg-type]
                A2ADataPart,
                "data",
                {"key": "value"},
            ),
            (
                FilePart(uri="https://example.com/f.pdf", mime_type="application/pdf"),
                A2AFilePart,
                "uri",
                "https://example.com/f.pdf",
            ),
        ],
        ids=["text", "data", "file"],
    )
    def test_part_mapping(
        self,
        part: TextPart | DataPart | FilePart,
        expected_cls: type,
        attr: str,
        expected_val: object,
    ) -> None:
        """Each internal part type maps to the correct A2A part."""
        msg = _make_message(parts=(part,))
        a2a = to_a2a(msg)
        assert len(a2a.parts) == 1
        assert isinstance(a2a.parts[0], expected_cls)
        assert getattr(a2a.parts[0], attr) == expected_val

    @pytest.mark.unit
    def test_uri_part_to_text(self) -> None:
        """UriPart (no A2A equivalent) maps to A2ATextPart."""
        msg = _make_message(parts=(UriPart(uri="https://example.com"),))
        a2a = to_a2a(msg)
        assert len(a2a.parts) == 1
        assert isinstance(a2a.parts[0], A2ATextPart)
        assert a2a.parts[0].text == "https://example.com"

    @pytest.mark.unit
    def test_multi_part(self) -> None:
        """Multiple parts are mapped preserving order."""
        msg = _make_message(
            parts=(
                TextPart(text="first"),
                DataPart(data={"n": 1}),  # type: ignore[arg-type]
                TextPart(text="last"),
            ),
        )
        a2a = to_a2a(msg)
        assert len(a2a.parts) == 3
        assert isinstance(a2a.parts[0], A2ATextPart)
        assert isinstance(a2a.parts[1], A2ADataPart)
        assert isinstance(a2a.parts[2], A2ATextPart)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("msg_type", "expected_role"),
        [
            (MessageType.DELEGATION, A2AMessageRole.USER),
            (MessageType.TASK_UPDATE, A2AMessageRole.AGENT),
            (MessageType.QUESTION, A2AMessageRole.AGENT),
        ],
    )
    def test_message_type_to_role(
        self,
        msg_type: MessageType,
        expected_role: A2AMessageRole,
    ) -> None:
        """Message types map to the correct A2A role."""
        msg = _make_message(msg_type=msg_type)
        a2a = to_a2a(msg)
        assert a2a.role == expected_role


class TestFromA2A:
    """A2AMessage -> Internal Message."""

    @pytest.mark.unit
    def test_text_part(self) -> None:
        """A2ATextPart maps to TextPart."""
        a2a = A2AMessage(
            role=A2AMessageRole.AGENT,
            parts=(A2ATextPart(text="response"),),
        )
        msg = from_a2a(
            a2a,
            channel="test",
            sender="external",
            recipient="internal",
        )
        assert len(msg.parts) == 1
        assert isinstance(msg.parts[0], TextPart)
        assert msg.parts[0].text == "response"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("role", "expected_type"),
        [
            (A2AMessageRole.USER, MessageType.DELEGATION),
            (A2AMessageRole.AGENT, MessageType.TASK_UPDATE),
        ],
    )
    def test_role_to_message_type(
        self,
        role: A2AMessageRole,
        expected_type: MessageType,
    ) -> None:
        """A2A role maps to the correct internal MessageType."""
        a2a = A2AMessage(
            role=role,
            parts=(A2ATextPart(text="test"),),
        )
        msg = from_a2a(
            a2a,
            channel="test",
            sender="external",
            recipient="internal",
        )
        assert msg.type == expected_type

    @pytest.mark.unit
    def test_sender_and_recipient_set(self) -> None:
        """Sender and recipient are set from args."""
        a2a = A2AMessage(
            role=A2AMessageRole.USER,
            parts=(A2ATextPart(text="test"),),
        )
        msg = from_a2a(
            a2a,
            channel="ch-1",
            sender="peer-a",
            recipient="agent-x",
        )
        assert msg.sender == "peer-a"
        assert msg.to == "agent-x"
        assert msg.channel == "ch-1"

    @pytest.mark.unit
    def test_data_part_round_trip(self) -> None:
        """DataPart survives A2A round-trip with data preserved."""
        a2a = A2AMessage(
            role=A2AMessageRole.AGENT,
            parts=(A2ADataPart(data={"result": [1, 2, 3]}),),
        )
        msg = from_a2a(
            a2a,
            channel="test",
            sender="s",
            recipient="r",
        )
        assert isinstance(msg.parts[0], DataPart)
        assert msg.parts[0].data["result"] == (1, 2, 3)

    @pytest.mark.unit
    def test_file_part_round_trip(self) -> None:
        """FilePart survives A2A round-trip with fields preserved."""
        a2a = A2AMessage(
            role=A2AMessageRole.AGENT,
            parts=(
                A2AFilePart(
                    uri="https://example.com/file.txt",
                    mime_type="text/plain",
                ),
            ),
        )
        msg = from_a2a(
            a2a,
            channel="test",
            sender="s",
            recipient="r",
        )
        assert isinstance(msg.parts[0], FilePart)
        assert msg.parts[0].uri == "https://example.com/file.txt"
        assert msg.parts[0].mime_type == "text/plain"


class TestMappingBoundary:
    """Deep copy and immutability at the protocol boundary."""

    @pytest.mark.unit
    def test_outbound_data_part_deep_copied(self) -> None:
        """Outbound DataPart data is deep-copied (not shared)."""
        from types import MappingProxyType

        original_data = {"key": "value", "count": 42}
        part = DataPart(data=MappingProxyType(original_data))
        msg = _make_message(parts=(part,))
        a2a = to_a2a(msg)
        a2a_data = a2a.parts[0]
        assert isinstance(a2a_data, A2ADataPart)
        assert a2a_data.data["key"] == "value"
        assert a2a_data.data["count"] == 42
        # Deep-copy: A2A dict is a distinct object from the original
        assert a2a_data.data is not original_data

    @pytest.mark.unit
    def test_inbound_data_part_wrapped_in_proxy(self) -> None:
        """Inbound A2ADataPart data is re-wrapped in MappingProxyType."""
        from types import MappingProxyType

        a2a = A2AMessage(
            role=A2AMessageRole.AGENT,
            parts=(A2ADataPart(data={"result": "ok"}),),
        )
        msg = from_a2a(
            a2a,
            channel="test",
            sender="s",
            recipient="r",
        )
        assert isinstance(msg.parts[0], DataPart)
        assert isinstance(msg.parts[0].data, MappingProxyType)
        assert msg.parts[0].data["result"] == "ok"

    @pytest.mark.unit
    def test_module_has_logger(self) -> None:
        """message_mapper module has the required logger."""
        from synthorg.a2a import message_mapper

        assert hasattr(message_mapper, "logger")

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("msg_type", "expected_metadata"),
        [
            (MessageType.TASK_UPDATE, "task_update"),
            (MessageType.DELEGATION, "delegation"),
            (MessageType.QUESTION, "question"),
        ],
    )
    def test_round_trip_preserves_message_type(
        self,
        msg_type: MessageType,
        expected_metadata: str,
    ) -> None:
        """Message type survives A2A round-trip via metadata."""
        msg = _make_message(msg_type=msg_type)
        a2a = to_a2a(msg)
        assert a2a.metadata.get(A2AMetadataKey.ORIG_MESSAGE_TYPE) == expected_metadata

        restored = from_a2a(
            a2a,
            channel="test",
            sender="s",
            recipient="r",
        )
        assert restored.type == msg_type

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("role", "metadata", "expected_type"),
        [
            (A2AMessageRole.AGENT, {}, MessageType.TASK_UPDATE),
            (A2AMessageRole.USER, {}, MessageType.DELEGATION),
            (
                A2AMessageRole.AGENT,
                {A2AMetadataKey.ORIG_MESSAGE_TYPE: "invalid_value"},
                MessageType.TASK_UPDATE,
            ),
            (
                A2AMessageRole.USER,
                {A2AMetadataKey.ORIG_MESSAGE_TYPE: "bogus"},
                MessageType.DELEGATION,
            ),
        ],
    )
    def test_from_a2a_heuristic_fallback(
        self,
        role: A2AMessageRole,
        metadata: dict[A2AMetadataKey, str],
        expected_type: MessageType,
    ) -> None:
        """from_a2a falls back to role heuristic for missing/invalid metadata."""
        a2a = A2AMessage(
            role=role,
            parts=(A2ATextPart(text="hi"),),
            metadata=metadata,
        )
        msg = from_a2a(
            a2a,
            channel="test",
            sender="ext",
            recipient="int",
        )
        assert msg.type == expected_type
