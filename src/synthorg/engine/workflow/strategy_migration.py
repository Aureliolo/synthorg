"""Strategy migration detection and notification.

When the ceremony scheduling strategy changes between sprints, the
system detects the change at ``activate_sprint()`` time and provides
a ``StrategyMigrationInfo`` result.  A separate ``notify_strategy_migration()``
function sends best-effort notifications via the communication system.
"""

from typing import TYPE_CHECKING, Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.communication.enums import MessagePriority, MessageType
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.ceremony_policy import (
    CeremonyStrategyType,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_CEREMONY_NOTIFICATION_FAILED,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from synthorg.communication.messenger import AgentMessenger

logger = get_logger(__name__)


class StrategyMigrationInfo(BaseModel):
    """Information about a ceremony strategy change between sprints.

    Produced by ``detect_strategy_migration()`` when the active strategy
    type changes.  The caller uses this to dispatch migration
    notifications via ``notify_strategy_migration()``.

    A strategy change always resets the velocity rolling-average window
    because each strategy uses a different velocity calculator with
    different units.

    Attributes:
        sprint_id: The sprint being activated.
        previous_strategy: The outgoing strategy type.
        new_strategy: The incoming strategy type.
        velocity_history_size: Count of velocity records accumulated
            under the outgoing strategy.  Used in notification messages
            to give the responsible role context on how much history is
            being superseded.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    sprint_id: NotBlankStr = Field(
        description="The sprint being activated",
    )
    previous_strategy: CeremonyStrategyType = Field(
        description="The outgoing strategy type",
    )
    new_strategy: CeremonyStrategyType = Field(
        description="The incoming strategy type",
    )
    velocity_history_size: int = Field(
        ge=0,
        description="Velocity records from old strategy",
    )

    @model_validator(mode="after")
    def _strategies_must_differ(self) -> Self:
        """Validate that previous and new strategies are different.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If ``previous_strategy == new_strategy``.
        """
        if self.previous_strategy == self.new_strategy:
            msg = (
                f"previous_strategy and new_strategy must differ,"
                f" got {self.previous_strategy!r} for both"
            )
            raise ValueError(msg)
        return self


def detect_strategy_migration(
    previous_strategy_type: CeremonyStrategyType | None,
    new_strategy_type: CeremonyStrategyType,
    sprint_id: NotBlankStr,
    velocity_history_size: int,
) -> StrategyMigrationInfo | None:
    """Detect a strategy change between sprints.

    Returns ``None`` on first activation (no previous strategy) or
    when the strategy type has not changed.

    Args:
        previous_strategy_type: The outgoing strategy type, or
            ``None`` on first activation.
        new_strategy_type: The incoming strategy type.
        sprint_id: The sprint being activated.
        velocity_history_size: Number of velocity records from the
            old strategy.

    Returns:
        Migration info if the strategy changed, else ``None``.
    """
    if previous_strategy_type is None:
        return None
    if previous_strategy_type == new_strategy_type:
        return None
    return StrategyMigrationInfo(
        sprint_id=sprint_id,
        previous_strategy=previous_strategy_type,
        new_strategy=new_strategy_type,
        velocity_history_size=velocity_history_size,
    )


def format_migration_warning(info: StrategyMigrationInfo) -> str:
    """Format the migration warning message text.

    Pure function -- no I/O, safe to call from any context.

    Args:
        info: The migration info.

    Returns:
        Human-readable warning text.
    """
    return (
        f"Ceremony scheduling strategy changed from"
        f" '{info.previous_strategy.value}' to"
        f" '{info.new_strategy.value}' for sprint"
        f" {info.sprint_id}. This change takes effect at"
        f" sprint start and may cause initial ceremony"
        f" optimization issues while the system adapts."
        f" The velocity rolling-average window has been"
        f" reset -- computed metrics from the"
        f" {info.velocity_history_size} prior sprint records"
        f" under '{info.previous_strategy.value}' will not"
        f" transfer to the new strategy. Raw velocity"
        f" records are preserved."
    )


def format_reorder_prompt(info: StrategyMigrationInfo) -> str:
    """Format the reorder action-item text.

    Pure function -- no I/O, safe to call from any context.

    Args:
        info: The migration info.

    Returns:
        Action-item text for the responsible role.
    """
    return (
        f"ACTION REQUIRED: Sprint {info.sprint_id} -- the"
        f" ceremony scheduling strategy has changed to"
        f" '{info.new_strategy.value}'. Please review and"
        f" reorder the sprint backlog to align with the new"
        f" strategy's optimization approach."
        f" {info.velocity_history_size} velocity records from"
        f" '{info.previous_strategy.value}' are preserved but"
        f" velocity-based ordering from the previous strategy"
        f" is no longer valid."
    )


async def _send_best_effort(
    coro: Awaitable[Any],
    info: StrategyMigrationInfo,
    note: str,
    **extra_context: str,
) -> None:
    """Await *coro*; swallow non-fatal errors.

    ``MemoryError`` and ``RecursionError`` propagate.  All other
    exceptions are logged at WARNING and swallowed.
    """
    try:
        await coro
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            SPRINT_CEREMONY_NOTIFICATION_FAILED,
            sprint_id=info.sprint_id,
            note=note,
            **extra_context,
        )


async def notify_strategy_migration(
    info: StrategyMigrationInfo,
    messenger: AgentMessenger,
    *,
    responsible_role: NotBlankStr = "scrum_master",
    channel: NotBlankStr = "#sprint-team",
) -> None:
    """Send migration notifications via the communication system.

    Best-effort: non-fatal errors are logged and swallowed.
    ``MemoryError`` and ``RecursionError`` propagate.

    Sends two messages:

    1. A broadcast ``ANNOUNCEMENT`` with the migration warning.
    2. A channel ``TASK_UPDATE`` with the reorder action item
       directed at the responsible role.

    Args:
        info: The migration info.
        messenger: Per-agent messenger facade.
        responsible_role: Agent role to receive the reorder prompt.
        channel: Channel for the reorder prompt message.

    Raises:
        MemoryError: Re-raised from either messenger call.
        RecursionError: Re-raised from either messenger call.
    """
    await _send_best_effort(
        messenger.broadcast(
            content=format_migration_warning(info),
            message_type=MessageType.ANNOUNCEMENT,
            priority=MessagePriority.HIGH,
        ),
        info,
        "broadcast notification failed",
    )
    await _send_best_effort(
        messenger.send_message(
            to=responsible_role,
            channel=channel,
            content=format_reorder_prompt(info),
            message_type=MessageType.TASK_UPDATE,
            priority=MessagePriority.HIGH,
        ),
        info,
        "reorder prompt notification failed",
        responsible_role=responsible_role,
        channel=channel,
    )
