"""Tests for the Attachment, MessageMetadata, and Message domain models."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from synthorg.communication.enums import (
    AttachmentType,
    MessagePriority,
    MessageType,
)
from synthorg.communication.message import Attachment, Message, MessageMetadata

pytestmark = pytest.mark.timeout(30)

# ── Helpers ──────────────────────────────────────────────────────

_MESSAGE_KWARGS: dict[str, object] = {
    "timestamp": datetime(2026, 2, 27, 10, 30, tzinfo=UTC),
    "sender": "sarah_chen",
    "to": "engineering",
    "type": MessageType.TASK_UPDATE,
    "channel": "#backend",
    "content": "PR ready for review.",
}


def _make_message(**overrides: object) -> Message:
    """Create a Message with sensible defaults, applying overrides."""
    kwargs = {**_MESSAGE_KWARGS, **overrides}
    return Message(**kwargs)  # type: ignore[arg-type]


# ── Attachment ──────────────────────────────────────────────────


@pytest.mark.unit
class TestAttachment:
    def test_construction(self) -> None:
        att = Attachment(type=AttachmentType.ARTIFACT, ref="pr-42")
        assert att.type is AttachmentType.ARTIFACT
        assert att.ref == "pr-42"

    def test_empty_ref_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Attachment(type=AttachmentType.FILE, ref="")

    def test_whitespace_ref_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            Attachment(type=AttachmentType.FILE, ref="   ")

    def test_frozen(self) -> None:
        att = Attachment(type=AttachmentType.LINK, ref="https://example.com")
        with pytest.raises(ValidationError):
            att.ref = "other"  # type: ignore[misc]

    def test_json_roundtrip(self) -> None:
        att = Attachment(type=AttachmentType.ARTIFACT, ref="pr-42")
        restored = Attachment.model_validate_json(att.model_dump_json())
        assert restored == att

    def test_model_dump(self) -> None:
        att = Attachment(type=AttachmentType.ARTIFACT, ref="pr-42")
        dumped = att.model_dump()
        assert dumped["type"] == "artifact"
        assert dumped["ref"] == "pr-42"

    def test_factory(self) -> None:
        from tests.unit.communication.conftest import AttachmentFactory

        att = AttachmentFactory.build()
        assert isinstance(att, Attachment)

    def test_model_copy(self) -> None:
        original = Attachment(type=AttachmentType.FILE, ref="readme.md")
        updated = original.model_copy(update={"ref": "changelog.md"})
        assert updated.ref == "changelog.md"
        assert original.ref == "readme.md"


# ── MessageMetadata ─────────────────────────────────────────────


@pytest.mark.unit
class TestMessageMetadataDefaults:
    def test_defaults(self) -> None:
        meta = MessageMetadata()
        assert meta.task_id is None
        assert meta.project_id is None
        assert meta.tokens_used is None
        assert meta.cost_usd is None
        assert meta.extra == ()

    def test_custom_values(self) -> None:
        meta = MessageMetadata(
            task_id="task-1",
            project_id="proj-1",
            tokens_used=500,
            cost_usd=0.05,
            extra=(("key1", "val1"),),
        )
        assert meta.task_id == "task-1"
        assert meta.project_id == "proj-1"
        assert meta.tokens_used == 500
        assert meta.cost_usd == 0.05
        assert meta.extra == (("key1", "val1"),)


@pytest.mark.unit
class TestMessageMetadataValidation:
    def test_empty_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MessageMetadata(task_id="")

    def test_whitespace_task_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            MessageMetadata(task_id="   ")

    def test_empty_project_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MessageMetadata(project_id="")

    def test_whitespace_project_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            MessageMetadata(project_id="   ")

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MessageMetadata(tokens_used=-1)

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MessageMetadata(cost_usd=-0.01)

    def test_zero_tokens_allowed(self) -> None:
        meta = MessageMetadata(tokens_used=0)
        assert meta.tokens_used == 0

    def test_zero_cost_allowed(self) -> None:
        meta = MessageMetadata(cost_usd=0.0)
        assert meta.cost_usd == 0.0

    def test_blank_extra_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra keys must not be blank"):
            MessageMetadata(extra=(("  ", "val"),))

    def test_duplicate_extra_keys_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate keys in extra"):
            MessageMetadata(extra=(("k", "v1"), ("k", "v2")))


@pytest.mark.unit
class TestMessageMetadataImmutability:
    def test_frozen(self) -> None:
        meta = MessageMetadata(task_id="task-1")
        with pytest.raises(ValidationError):
            meta.task_id = "task-2"  # type: ignore[misc]

    def test_model_copy(self) -> None:
        original = MessageMetadata(task_id="task-1")
        updated = original.model_copy(update={"task_id": "task-2"})
        assert updated.task_id == "task-2"
        assert original.task_id == "task-1"


@pytest.mark.unit
class TestMessageMetadataSerialization:
    def test_json_roundtrip(self) -> None:
        meta = MessageMetadata(
            task_id="task-1",
            project_id="proj-1",
            tokens_used=100,
            cost_usd=0.01,
            extra=(("env", "prod"),),
        )
        restored = MessageMetadata.model_validate_json(meta.model_dump_json())
        assert restored == meta

    def test_factory(self) -> None:
        from tests.unit.communication.conftest import MessageMetadataFactory

        meta = MessageMetadataFactory.build()
        assert isinstance(meta, MessageMetadata)


# ── Message ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestMessageConstruction:
    def test_minimal_valid(self) -> None:
        msg = _make_message()
        assert isinstance(msg.id, UUID)
        assert msg.sender == "sarah_chen"
        assert msg.to == "engineering"
        assert msg.type is MessageType.TASK_UPDATE
        assert msg.channel == "#backend"
        assert msg.content == "PR ready for review."

    def test_default_values(self) -> None:
        msg = _make_message()
        assert msg.priority is MessagePriority.NORMAL
        assert msg.attachments == ()
        assert isinstance(msg.metadata, MessageMetadata)

    def test_all_fields_set(self) -> None:
        meta = MessageMetadata(task_id="task-1")
        att = Attachment(type=AttachmentType.ARTIFACT, ref="pr-42")
        msg = Message(
            timestamp=datetime(2026, 2, 27, 10, 30, tzinfo=UTC),
            sender="sarah_chen",
            to="engineering",
            type=MessageType.REVIEW_REQUEST,
            priority=MessagePriority.HIGH,
            channel="#code-review",
            content="Please review PR-42.",
            attachments=(att,),
            metadata=meta,
        )
        assert msg.priority is MessagePriority.HIGH
        assert len(msg.attachments) == 1
        assert msg.metadata.task_id == "task-1"


@pytest.mark.unit
class TestMessageUniqueIds:
    def test_unique_ids(self) -> None:
        msg1 = _make_message()
        msg2 = _make_message()
        assert msg1.id != msg2.id


@pytest.mark.unit
class TestMessageAlias:
    def test_alias_from_parsing(self) -> None:
        """Parse JSON with 'from' key (DESIGN_SPEC 5.3 format)."""
        data = {
            "timestamp": "2026-02-27T10:30:00Z",
            "from": "sarah_chen",
            "to": "engineering",
            "type": "task_update",
            "channel": "#backend",
            "content": "Hello.",
        }
        msg = Message.model_validate(data)
        assert msg.sender == "sarah_chen"

    def test_populate_by_name(self) -> None:
        """Parse JSON with 'sender' key (populate_by_name=True)."""
        data = {
            "timestamp": "2026-02-27T10:30:00Z",
            "sender": "sarah_chen",
            "to": "engineering",
            "type": "task_update",
            "channel": "#backend",
            "content": "Hello.",
        }
        msg = Message.model_validate(data)
        assert msg.sender == "sarah_chen"

    def test_dump_by_alias(self) -> None:
        """model_dump(by_alias=True) outputs 'from' key."""
        msg = _make_message()
        dumped = msg.model_dump(by_alias=True)
        assert "from" in dumped
        assert dumped["from"] == "sarah_chen"

    def test_dump_by_name(self) -> None:
        """model_dump() outputs 'sender' key."""
        msg = _make_message()
        dumped = msg.model_dump()
        assert "sender" in dumped
        assert dumped["sender"] == "sarah_chen"


@pytest.mark.unit
class TestMessageAliasRoundtrip:
    def test_json_roundtrip_with_alias(self) -> None:
        """Ensure JSON with 'from' key (DESIGN_SPEC 5.3 format) round-trips."""
        msg = _make_message()
        json_str = msg.model_dump_json(by_alias=True)
        assert '"from"' in json_str
        restored = Message.model_validate_json(json_str)
        assert restored == msg


@pytest.mark.unit
class TestMessageStringValidation:
    def test_empty_sender_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_message(sender="")

    def test_whitespace_sender_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_message(sender="   ")

    def test_empty_to_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_message(to="")

    def test_whitespace_to_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_message(to="   ")

    def test_empty_channel_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_message(channel="")

    def test_whitespace_channel_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_message(channel="   ")

    def test_empty_content_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_message(content="")

    def test_whitespace_content_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace-only"):
            _make_message(content="   ")


@pytest.mark.unit
class TestMessageImmutability:
    def test_frozen(self) -> None:
        msg = _make_message()
        with pytest.raises(ValidationError):
            msg.content = "new"  # type: ignore[misc]

    def test_model_copy(self) -> None:
        original = _make_message()
        updated = original.model_copy(update={"content": "Updated content."})
        assert updated.content == "Updated content."
        assert original.content == "PR ready for review."


@pytest.mark.unit
class TestMessageSerialization:
    def test_json_roundtrip(self) -> None:
        msg = _make_message(
            attachments=(Attachment(type=AttachmentType.ARTIFACT, ref="pr-42"),),
            metadata=MessageMetadata(task_id="task-1"),
        )
        json_str = msg.model_dump_json()
        restored = Message.model_validate_json(json_str)
        assert restored.id == msg.id
        assert restored.sender == msg.sender
        assert restored.type is msg.type
        assert restored.attachments == msg.attachments
        assert restored.metadata == msg.metadata

    def test_model_dump_enum_values(self) -> None:
        msg = _make_message()
        dumped = msg.model_dump()
        assert dumped["type"] == "task_update"
        assert dumped["priority"] == "normal"


@pytest.mark.unit
class TestMessageFactory:
    def test_factory(self) -> None:
        from tests.unit.communication.conftest import MessageFactory

        msg = MessageFactory.build()
        assert isinstance(msg, Message)
        assert isinstance(msg.id, UUID)
        assert isinstance(msg.type, MessageType)


@pytest.mark.unit
class TestMessageFixtures:
    def test_sample_message(self, sample_message: Message) -> None:
        assert sample_message.sender == "sarah_chen"
        assert sample_message.to == "engineering"
        assert sample_message.type is MessageType.TASK_UPDATE
        assert len(sample_message.attachments) == 1

    def test_sample_attachment(self, sample_attachment: Attachment) -> None:
        assert sample_attachment.type is AttachmentType.ARTIFACT
        assert sample_attachment.ref == "pr-42"

    def test_sample_metadata(self, sample_metadata: MessageMetadata) -> None:
        assert sample_metadata.task_id == "task-123"
        assert sample_metadata.tokens_used == 1200
