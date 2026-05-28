"""Message domain models (see Communication design page)."""

from collections import Counter
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, Literal, Self

if TYPE_CHECKING:
    from collections.abc import Mapping
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    computed_field,
    field_serializer,
    field_validator,
    model_validator,
)

from synthorg.communication.enums import (
    MessagePriority,
    MessageType,
)
from synthorg.core.immutable import deep_copy_mapping, freeze_recursive
from synthorg.core.types import (
    NotBlankStr,
)
from synthorg.ontology.decorator import ontology_entity

# ── Part types ────────────────────────────────────────────────────


class TextPart(BaseModel):
    """Plain text message content.

    Attributes:
        type: Discriminator literal (always ``"text"``).
        text: The text content (must not be blank).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: Literal["text"] = Field(
        default="text",
        description="Part type discriminator",
    )
    text: NotBlankStr = Field(description="Text content")


class DataPart(BaseModel):
    """Structured JSON data attached to a message.

    The ``data`` field is deep-copied and recursively frozen at
    construction (``dict`` -> ``MappingProxyType``, ``list`` ->
    ``tuple``, ``set`` -> ``frozenset``) to preserve the immutability
    contract of frozen Pydantic models.

    Attributes:
        type: Discriminator literal (always ``"data"``).
        data: Structured JSON content (read-only after construction).
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    type: Literal["data"] = Field(
        default="data",
        description="Part type discriminator",
    )
    data: MappingProxyType[str, Any] = Field(
        description="Structured JSON content (read-only)",
    )

    @field_validator("data", mode="before")
    @classmethod
    def _deep_copy_and_freeze_data(cls, value: object) -> object:
        """Deep-copy and recursively freeze data.

        Follows the same pattern as
        ``DecisionRecord._deep_copy_and_freeze_metadata``.
        """
        if isinstance(value, MappingProxyType):
            value = dict(value)
        copied = deep_copy_mapping(value)
        return freeze_recursive(copied)

    @field_serializer("data")
    @classmethod
    def _serialize_data(
        cls,
        value: MappingProxyType[str, Any],
        _info: object,
    ) -> dict[str, Any]:
        """Serialize ``MappingProxyType`` back to a plain ``dict``.

        Recursively thaws nested ``MappingProxyType`` instances and
        converts ``tuple``/``frozenset`` back to ``list`` so the
        result is JSON-serializable.
        """

        def _thaw(current: object) -> object:
            if isinstance(current, MappingProxyType):
                return {k: _thaw(v) for k, v in current.items()}
            if isinstance(current, (tuple, frozenset)):
                return [_thaw(item) for item in current]
            return current

        return _thaw(value)  # type: ignore[return-value]

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        """Override to re-freeze data when model_copy updates it."""
        result = super().model_copy(update=update, deep=deep)
        if update and "data" in update:
            raw = update["data"]
            if isinstance(raw, MappingProxyType):
                raw = dict(raw)
            frozen = freeze_recursive(deep_copy_mapping(raw))
            object.__setattr__(result, "data", frozen)
        return result


class FilePart(BaseModel):
    """Reference to a file resource.

    Attributes:
        type: Discriminator literal (always ``"file"``).
        uri: File URI or path.
        mime_type: Optional MIME type of the file.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: Literal["file"] = Field(
        default="file",
        description="Part type discriminator",
    )
    uri: NotBlankStr = Field(description="File URI or path")
    mime_type: NotBlankStr | None = Field(
        default=None,
        description="Optional MIME type",
    )


class UriPart(BaseModel):
    """Reference to an external URI resource.

    Attributes:
        type: Discriminator literal (always ``"uri"``).
        uri: The URI or URL.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: Literal["uri"] = Field(
        default="uri",
        description="Part type discriminator",
    )
    uri: NotBlankStr = Field(description="URI or URL")


Part = Annotated[
    TextPart | DataPart | FilePart | UriPart,
    Discriminator("type"),
]
"""Discriminated union of message content parts.

Pydantic uses the ``type`` literal field on each part to determine
which subtype to deserialize into.
"""


# ── Metadata ──────────────────────────────────────────────────────


class MessageMetadata(BaseModel):
    """Optional metadata carried with a message.

    Extends the Communication design page metadata with an additional ``extra``
    field for arbitrary key-value pairs.

    Attributes:
        task_id: Related task identifier.
        project_id: Related project identifier.
        tokens_used: LLM tokens consumed producing the message.
        cost: Estimated cost of the message in the configured currency.
        extra: Immutable key-value pairs for arbitrary metadata (extension).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    cost: float | None = Field(
        default=None,
        ge=0.0,
        description="Estimated cost in the configured currency",
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


# ── Message ───────────────────────────────────────────────────────


@ontology_entity
class Message(BaseModel):
    """An inter-agent message.

    Field schema is based on the Communication design page with typed
    refinements.  The ``sender`` field is aliased to ``"from"`` for
    JSON compatibility with the spec format.

    Content is represented as a tuple of typed ``Part`` objects
    (text, data, file, URI) rather than a flat string, enabling rich
    multi-part messages.

    Attributes:
        id: Unique message identifier.
        timestamp: When the message was created (must be timezone-aware).
        sender: Agent ID of the sender (aliased to ``"from"`` in JSON).
        to: Recipient agent or channel identifier.
        type: Message type classification.
        priority: Message priority level.
        channel: Channel the message is sent through.
        parts: Ordered content parts (text, data, files, URIs).
        metadata: Optional message metadata.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        allow_inf_nan=False,
        arbitrary_types_allowed=True,
    )

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
    parts: tuple[Part, ...] = Field(
        min_length=1,
        description="Ordered content parts (text, data, files, URIs)",
    )
    attachments: tuple[Part, ...] = Field(
        default=(),
        description=(
            "Out-of-band parts attached to the message (files, data "
            "blobs, URIs) that are not part of the primary content "
            "flow. Persisted alongside the message and round-tripped "
            "by both backends."
        ),
    )
    metadata: MessageMetadata = Field(
        default_factory=MessageMetadata,
        description="Optional message metadata",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text(self) -> str:
        """Extract the first ``TextPart`` text, or empty string.

        Convenience accessor for consumers that only need the primary
        text content of a message.
        """
        for part in self.parts:
            if isinstance(part, TextPart):
                return part.text
        return ""
