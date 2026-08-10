# module-kind: code
"""Turning a meeting's action items into tasks.

Separate from the orchestrator because it shares no state with the meeting
lifecycle: it reads the action items a protocol produced, the per-meeting
cap, and the task-creator callback, and reports what it managed to create.
Task creation is also failure-tolerant in a way meeting execution is not:
a creator that raises costs one task, never the meeting record.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.communication.meeting.config import MeetingProtocolConfig
from synthorg.communication.meeting.models import MeetingMinutes
from synthorg.communication.meeting.protocol import TaskCreator
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meeting import (
    MEETING_ACTION_ITEM_EXTRACTED,
    MEETING_TASK_CREATED,
    MEETING_TASK_CREATION_FAILED,
    MEETING_TASK_CREATION_SUMMARY,
    MEETING_TASKS_CAPPED,
)

logger = get_logger(__name__)


class TaskCreationOutcome(BaseModel):
    """How many of a meeting's action items became tasks.

    Reported back to the meeting record because a dropped action item is
    invisible otherwise: the meeting still completes, and only the log
    stream knows that what it decided to do was never scheduled.

    Attributes:
        created: Action items that became tasks.
        failed: Action items whose creation raised.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    created: int = Field(default=0, ge=0, description="Tasks created")
    failed: int = Field(default=0, ge=0, description="Action items dropped")


def create_tasks_from_action_items(
    task_creator: TaskCreator | None,
    *,
    meeting_id: str,
    protocol_config: MeetingProtocolConfig,
    minutes: MeetingMinutes,
) -> TaskCreationOutcome:
    """Create tasks from a meeting's action items, if configured.

    A no-op when no creator is wired, when the meeting's config switches
    auto-creation off, or when the protocol surfaced no action items.

    Args:
        task_creator: Callback that creates one task, or ``None``.
        meeting_id: The meeting the action items came from.
        protocol_config: The meeting's own config, carrying
            ``auto_create_tasks`` and ``max_tasks_per_meeting``.
        minutes: The minutes the protocol produced.

    Returns:
        How many action items became tasks, and how many were dropped.
    """
    if (
        task_creator is None
        or not protocol_config.auto_create_tasks
        or not minutes.action_items
    ):
        return TaskCreationOutcome()

    items = minutes.action_items
    cap = protocol_config.max_tasks_per_meeting
    if cap is not None and len(items) > cap:
        logger.info(
            MEETING_TASKS_CAPPED,
            meeting_id=meeting_id,
            total_action_items=len(items),
            max_tasks_per_meeting=cap,
        )
        items = items[:cap]

    total = len(items)
    logger.info(
        MEETING_ACTION_ITEM_EXTRACTED,
        meeting_id=meeting_id,
        action_item_count=total,
    )
    failures = 0
    for action_item in items:
        try:
            task_creator(
                action_item.description,
                action_item.assignee_id,
                action_item.priority,
            )
            logger.debug(
                MEETING_TASK_CREATED,
                meeting_id=meeting_id,
                description=action_item.description,
                assignee=action_item.assignee_id,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            failures += 1
            log_exception_redacted(
                logger,
                MEETING_TASK_CREATION_FAILED,
                exc,
                meeting_id=meeting_id,
                description=action_item.description,
                assignee=action_item.assignee_id,
            )
    if failures:
        # Its own event: sharing the per-item name would make any count
        # of that event add the per-item errors to the per-meeting
        # summary, under two different field shapes.
        logger.warning(
            MEETING_TASK_CREATION_SUMMARY,
            meeting_id=meeting_id,
            failed_count=failures,
            total_count=total,
        )
    return TaskCreationOutcome(created=total - failures, failed=failures)
