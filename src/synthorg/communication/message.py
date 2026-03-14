"""Message domain models (see Communication design page)."""

from collections import Counter
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.communication.enums import (
    AttachmentType,
    MessagePriority,
    MessageType,
)
from synthorg.core.types import (
    NotBlankStr,  # noqa: TC001 -- required at runtime by Pydantic
)


class Attachment(BaseModel):
    """A reference attached to a message.

    Attributes:
        type: The kind of attachment.
        ref: Reference identifier (e.g. artifact ID, URL, file path).
    """

    model_config = ConfigDict(frozen=True)

    type: AttachmentType = Field(description="Kind of attachment")
    ref: NotBlankStr = Field(description="Reference identifier")


class MessageMetadata(BaseModel):
    """Optional metadata carried with a message.

    Extends the Communication design page metadata with an additional ``extra``
    field for arbitrary key-value pairs.

    Attributes:
        task_id: Related task identifier.
        project_id: Related project identifier.
        tokens_used: LLM tokens consumed producing the message.
        cost_usd: Estimated USD cost of the message.
        extra: Immutable key-value pairs for arbitrary metadata (extension).
    """

    model_config = ConfigDict(frozen=True)

    task_id: NotBlankStr | None = Field(
        default=None,
        description="Related task identifier",
    )
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Related project identifier",
    )
    tokens_used: int | None = Field(
        default=None,
        ge=0,
        description="LLM tokens consumed",
    )
    cost_usd: float | None = Field(
        default=None,
        ge=0.0,
        description="Estimated USD cost",
    )
    extra: tuple[tuple[str, str], ...] = Field(
        default=(),
        description="Immutable key-value pairs for arbitrary metadata",
    )

    @model_validator(mode="after")
    def _validate_extra(self) -> Self:
        """Ensure extra keys are non-blank and unique."""
        keys: list[str] = []
        for key, _value in self.extra:
            if not key.strip():
                msg = "extra keys must not be blank"
                raise ValueError(msg)
            keys.append(key)
        if len(keys) != len(set(keys)):
            dupes = sorted(k for k, c in Counter(keys).items() if c > 1)
            msg = f"Duplicate keys in extra: {dupes}"
            raise ValueError(msg)
        return self


class Message(BaseModel):
    """An inter-agent message.

    Field schema is based on the Communication design page with typed refinements.
    The ``sender`` field is aliased to ``"from"`` for JSON compatibility with
    the spec format.

    Attributes:
        id: Unique message identifier.
        timestamp: When the message was created (must be timezone-aware).
        sender: Agent ID of the sender (aliased to ``"from"`` in JSON).
        to: Recipient agent or channel identifier.
        type: Message type classification.
        priority: Message priority level.
        channel: Channel the message is sent through.
        content: Message body text.
        attachments: Attached references.
        metadata: Optional message metadata.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID = Field(
        default_factory=uuid4,
        description="Unique message identifier",
    )
    timestamp: AwareDatetime = Field(
        description="When the message was created (must be timezone-aware)",
    )
    sender: NotBlankStr = Field(
        alias="from",
        description="Sender agent ID",
    )
    to: NotBlankStr = Field(description="Recipient agent or channel")
    type: MessageType = Field(description="Message type classification")
    priority: MessagePriority = Field(
        default=MessagePriority.NORMAL,
        description="Message priority level",
    )
    channel: NotBlankStr = Field(
        description="Channel the message is sent through",
    )
    content: NotBlankStr = Field(description="Message body text")
    attachments: tuple[Attachment, ...] = Field(
        default=(),
        description="Attached references",
    )
    metadata: MessageMetadata = Field(
        default_factory=MessageMetadata,
        description="Optional message metadata",
    )
