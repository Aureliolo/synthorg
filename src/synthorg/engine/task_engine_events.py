"""Event building and snapshot publishing for the TaskEngine.

Extracted from ``task_engine.py`` to keep the main module
under the 800-line project limit.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.task_engine_models import (
    DeleteTaskMutation,
    TaskMutationResult,
    TaskStateChanged,
)
from synthorg.observability import get_logger
from synthorg.observability.events.task_engine import (
    TASK_ENGINE_SNAPSHOT_PUBLISH_FAILED,
    TASK_ENGINE_SNAPSHOT_PUBLISHED,
)

if TYPE_CHECKING:
    from synthorg.communication.bus_protocol import MessageBus
    from synthorg.engine.task_engine_models import TaskMutation

logger = get_logger(__name__)


def build_state_changed_event(
    mutation: TaskMutation,
    result: TaskMutationResult,
) -> TaskStateChanged:
    """Build a ``TaskStateChanged`` event from a mutation result.

    Shared by snapshot publishing and observer notification.

    Returns:
        The :class:`TaskStateChanged` event carrying request id, task
        id, mutation type, the new status (``None`` for delete or
        failed mutation), and optional reason.
    """
    if isinstance(mutation, DeleteTaskMutation):
        new_status = None
    elif result.task is not None:
        new_status = result.task.status
    else:
        new_status = None

    reason: str | None = getattr(mutation, "reason", None)
    task_id: str | None = getattr(mutation, "task_id", None)
    # For create mutations, task_id comes from the result
    if task_id is None and result.task is not None:
        task_id = result.task.id
    effective_task_id = task_id or "unknown"

    return TaskStateChanged(
        mutation_type=mutation.mutation_type,
        request_id=mutation.request_id,
        requested_by=mutation.requested_by,
        task_id=effective_task_id,
        task=result.task,
        previous_status=result.previous_status,
        new_status=new_status,
        version=result.version,
        reason=reason,
        timestamp=datetime.now(UTC),
    )


async def publish_snapshot(
    mutation: TaskMutation,
    event: TaskStateChanged,
    *,
    message_bus: MessageBus | None,
    sender: str,
    channel: str,
) -> None:
    """Publish a TaskStateChanged event to the message bus.

    Best-effort: failures are logged and swallowed (except
    ``MemoryError`` and ``RecursionError``, which propagate).
    """
    if message_bus is None:
        return

    try:
        # Deferred to break circular import:
        # communication -> engine -> communication
        from synthorg.communication.enums import MessageType  # noqa: PLC0415
        from synthorg.communication.message import Message, TextPart  # noqa: PLC0415

        msg = Message(
            timestamp=datetime.now(UTC),
            sender=sender,
            to=channel,
            type=MessageType.TASK_UPDATE,
            channel=channel,
            parts=(TextPart(text=event.model_dump_json()),),
        )
        await message_bus.publish(msg)
        logger.debug(
            TASK_ENGINE_SNAPSHOT_PUBLISHED,
            mutation_type=mutation.mutation_type,
            request_id=mutation.request_id,
            task_id=event.task_id,
        )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            TASK_ENGINE_SNAPSHOT_PUBLISH_FAILED,
            mutation_type=mutation.mutation_type,
            request_id=mutation.request_id,
            task_id=event.task_id,
        )
