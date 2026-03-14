"""Shared fixtures and helpers for persistence unit tests."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.communication.message import Message, MessageMetadata
from synthorg.core.enums import Priority, TaskStatus, TaskType
from synthorg.core.task import Task

if TYPE_CHECKING:
    from uuid import UUID


_REQUIRES_ASSIGNEE = frozenset(
    {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.IN_REVIEW,
        TaskStatus.COMPLETED,
    }
)


def make_task(  # noqa: PLR0913
    *,
    task_id: str = "task-001",
    title: str = "Test task",
    description: str = "A test task for persistence",
    task_type: TaskType = TaskType.DEVELOPMENT,
    priority: Priority = Priority.MEDIUM,
    project: str = "test-project",
    created_by: str = "alice",
    assigned_to: str | None = None,
    status: TaskStatus = TaskStatus.CREATED,
) -> Task:
    """Build a Task with sensible defaults for persistence tests.

    Automatically fills ``assigned_to`` with ``"alice"`` when the
    status requires an assignee and none was provided.
    """
    effective_assigned_to = assigned_to
    if effective_assigned_to is None and status in _REQUIRES_ASSIGNEE:
        effective_assigned_to = "alice"
    return Task(
        id=task_id,
        title=title,
        description=description,
        type=task_type,
        priority=priority,
        project=project,
        created_by=created_by,
        assigned_to=effective_assigned_to,
        status=status,
    )


def make_message(  # noqa: PLR0913
    *,
    msg_id: UUID | None = None,
    sender: str = "alice",
    to: str = "bob",
    channel: str = "general",
    content: str = "Hello, world!",
    msg_type: MessageType = MessageType.TASK_UPDATE,
    priority: MessagePriority = MessagePriority.NORMAL,
    timestamp: datetime | None = None,
    metadata: MessageMetadata | None = None,
) -> Message:
    """Build a Message with sensible defaults for persistence tests."""
    kwargs: dict[str, object] = {
        "from": sender,
        "to": to,
        "channel": channel,
        "content": content,
        "type": msg_type,
        "priority": priority,
        "timestamp": timestamp or datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
    }
    if msg_id is not None:
        kwargs["id"] = msg_id
    if metadata is not None:
        kwargs["metadata"] = metadata
    return Message.model_validate(kwargs)
