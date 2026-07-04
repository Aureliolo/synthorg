"""Shared-namespace constants and publisher/ownership helpers for Mem0.

The metadata prefix and shared-namespace ``user_id`` are defined here so
both the bidirectional mappers and the post-retrieval filters can import
them without a circular dependency.  Publisher attribution and delete
ownership checks live alongside the constants they depend on.
"""

from collections.abc import Mapping
from typing import NamedTuple

from synthorg.core.types import NotBlankStr
from synthorg.memory.errors import MemoryStoreError
from synthorg.observability import get_logger
from synthorg.observability.events.memory import (
    MEMORY_ENTRY_DELETE_FAILED,
    MEMORY_ENTRY_UPDATE_FAILED,
    MEMORY_MODEL_INVALID,
)

logger = get_logger(__name__)

# Metadata prefix avoids collisions with Mem0's own keys.
_PREFIX = "_synthorg_"

# Metadata key to track who published a shared memory.
# Public because the adapter module needs it for ownership tracking.
PUBLISHER_KEY: str = f"{_PREFIX}publisher"

# Reserved user_id for the shared knowledge namespace.
# All shared memories are stored under this Mem0 ``user_id`` so they
# are isolated from per-agent memories and can be queried centrally.
SHARED_NAMESPACE: str = "__synthorg_shared__"


def resolve_publisher(item: Mapping[str, object]) -> str:
    """Extract publisher from a shared memory, defaulting to namespace.

    Logs at DEBUG when publisher metadata is missing.

    Returns:
        Result of type ``str``.
    """
    publisher = extract_publisher(item)
    if publisher is None:
        logger.debug(
            MEMORY_MODEL_INVALID,
            memory_id=item.get("id", "?"),
            reason="no publisher metadata -- attributing to shared namespace",
        )
        return SHARED_NAMESPACE
    return publisher


def extract_publisher(raw: Mapping[str, object]) -> NotBlankStr | None:
    """Extract the publisher agent ID from a shared memory dict.

    Returns ``None`` if the publisher key is missing, non-mapping
    metadata, or the value is blank after coercion and stripping.

    Returns:
        The resulting ``NotBlankStr``, or ``None`` when unavailable.
    """
    metadata = raw.get("metadata", {})
    if not metadata or not isinstance(metadata, Mapping):
        return None
    value = metadata.get(PUBLISHER_KEY)
    if value is None:
        return None
    coerced = str(value).strip()
    return NotBlankStr(coerced) if coerced else None


class _OwnershipMessages(NamedTuple):
    """Phrasing for one ownership-check caller (delete vs. update)."""

    action_noun: str
    action_verb: str
    shared_namespace_hint: str
    fail_event: str


_DELETE_MESSAGES = _OwnershipMessages(
    action_noun="deletion",
    action_verb="delete",
    shared_namespace_hint="use retract() to remove shared entries",
    fail_event=MEMORY_ENTRY_DELETE_FAILED,
)
_UPDATE_MESSAGES = _OwnershipMessages(
    action_noun="update",
    action_verb="update",
    shared_namespace_hint="shared entries cannot be updated directly",
    fail_event=MEMORY_ENTRY_UPDATE_FAILED,
)


def _check_ownership(
    existing: Mapping[str, object],
    agent_id: NotBlankStr,
    memory_id: NotBlankStr,
    messages: _OwnershipMessages,
) -> None:
    """Verify the caller owns this private memory entry.

    Shared by ``check_delete_ownership`` and ``check_update_ownership``
    since mem0's ``update()``/``delete()``/``get()`` take no
    ``user_id`` filter -- both mutations must fetch the entry first
    and verify ownership the same way.

    Raises:
        MemoryStoreError: If ownership cannot be verified
            (missing user_id, shared namespace entry, or
            ownership mismatch).
    """
    owner = existing.get("user_id")
    if owner is None:
        msg = (
            f"Memory {memory_id} has no user_id -- ownership "
            f"unverifiable, refusing {messages.action_noun}"
        )
        logger.warning(
            messages.fail_event,
            agent_id=agent_id,
            memory_id=memory_id,
            reason="unverifiable_ownership",
        )
        raise MemoryStoreError(msg)
    if str(owner) == SHARED_NAMESPACE:
        msg = (
            f"Memory {memory_id} belongs to the shared namespace -- "
            f"{messages.shared_namespace_hint}"
        )
        logger.warning(
            messages.fail_event,
            agent_id=agent_id,
            memory_id=memory_id,
            reason="shared namespace entry",
        )
        raise MemoryStoreError(msg)
    if str(owner) != str(agent_id):
        msg = (
            f"Agent {agent_id} cannot {messages.action_verb} memory "
            f"{memory_id} owned by {owner}"
        )
        logger.warning(
            messages.fail_event,
            agent_id=agent_id,
            memory_id=memory_id,
            reason="ownership mismatch",
            actual_owner=str(owner),
        )
        raise MemoryStoreError(msg)


def check_delete_ownership(
    existing: Mapping[str, object],
    agent_id: NotBlankStr,
    memory_id: NotBlankStr,
) -> None:
    """Verify the caller owns this private memory entry before deletion.

    Raises:
        MemoryStoreError: If ownership cannot be verified.
    """
    _check_ownership(existing, agent_id, memory_id, _DELETE_MESSAGES)


def check_update_ownership(
    existing: Mapping[str, object],
    agent_id: NotBlankStr,
    memory_id: NotBlankStr,
) -> None:
    """Verify the caller owns this private memory entry before update.

    Raises:
        MemoryStoreError: If ownership cannot be verified.
    """
    _check_ownership(existing, agent_id, memory_id, _UPDATE_MESSAGES)
