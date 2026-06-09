"""Bidirectional message mapping between SynthOrg and A2A.

Maps ``synthorg.communication.message.Message`` to/from
``synthorg.a2a.models.A2AMessage``.  Part types are mapped
directly; ``UriPart`` (no A2A equivalent) is converted to text.
"""

import copy
from datetime import UTC, datetime
from types import MappingProxyType
from typing import cast
from uuid import uuid4

from pydantic import JsonValue

from synthorg.a2a.models import (
    A2ADataPart,
    A2AFilePart,
    A2AMessage,
    A2AMessagePart,
    A2AMessageRole,
    A2AMetadataKey,
    A2ATextPart,
)
from synthorg.communication.enums import MessageType
from synthorg.communication.message import (
    DataPart,
    FilePart,
    Message,
    Part,
    TextPart,
)
from synthorg.observability import get_logger

logger = get_logger(__name__)


def _internal_part_to_a2a(part: Part) -> A2AMessagePart:
    """Map a single internal Part to an A2A part.

    Args:
        part: Internal message part.

    Returns:
        A2A message part.
    """
    if isinstance(part, TextPart):
        return A2ATextPart(text=part.text)
    if isinstance(part, DataPart):
        raw = part.data
        source = dict(raw) if isinstance(raw, MappingProxyType) else raw
        # Message ``DataPart.data`` holds opaque JSON (typed ``object``);
        # the A2A wire model narrows it to ``JsonValue`` at this boundary.
        return A2ADataPart(
            data=cast("dict[str, JsonValue]", copy.deepcopy(source)),
        )
    if isinstance(part, FilePart):
        return A2AFilePart(
            uri=part.uri,
            mime_type=part.mime_type,
        )
    # UriPart: no A2A equivalent; convert to text
    return A2ATextPart(text=part.uri)


def _a2a_part_to_internal(part: A2AMessagePart) -> Part:
    """Map a single A2A part to an internal Part.

    Args:
        part: A2A message part.

    Returns:
        Internal message part.
    """
    if isinstance(part, A2ATextPart):
        return TextPart(text=part.text)
    if isinstance(part, A2ADataPart):
        return DataPart(data=MappingProxyType(copy.deepcopy(part.data)))
    # A2AFilePart
    return FilePart(uri=part.uri, mime_type=part.mime_type)


def to_a2a(message: Message) -> A2AMessage:
    """Map an internal Message to an A2A Message.

    The sender's role is inferred from the message type:
    ``DELEGATION`` maps to ``user``, everything else maps to
    ``agent``.

    Args:
        message: Internal message.

    Returns:
        A2A message.
    """
    role = (
        A2AMessageRole.USER
        if message.type == MessageType.DELEGATION
        else A2AMessageRole.AGENT
    )
    parts = tuple(_internal_part_to_a2a(p) for p in message.parts)
    return A2AMessage(
        role=role,
        parts=parts,
        metadata={A2AMetadataKey.ORIG_MESSAGE_TYPE: message.type.value},
    )


def from_a2a(
    a2a_msg: A2AMessage,
    *,
    channel: str,
    sender: str,
    recipient: str,
) -> Message:
    """Map an A2A Message to an internal Message.

    Args:
        a2a_msg: A2A message.
        channel: Internal channel name.
        sender: Sender identifier.
        recipient: Recipient identifier.

    Returns:
        Internal message.
    """
    # Restore original MessageType from metadata when available;
    # fall back to role-based heuristic for external messages.
    orig_type = a2a_msg.metadata.get(A2AMetadataKey.ORIG_MESSAGE_TYPE, "")
    if orig_type:
        try:
            msg_type = MessageType(orig_type)
        except ValueError:
            msg_type = (
                MessageType.DELEGATION
                if a2a_msg.role == A2AMessageRole.USER
                else MessageType.TASK_UPDATE
            )
    else:
        msg_type = (
            MessageType.DELEGATION
            if a2a_msg.role == A2AMessageRole.USER
            else MessageType.TASK_UPDATE
        )
    parts = tuple(_a2a_part_to_internal(p) for p in a2a_msg.parts)
    return Message(
        id=uuid4(),
        timestamp=datetime.now(UTC),
        sender=sender,
        to=recipient,
        type=msg_type,
        channel=channel,
        parts=parts,
    )
