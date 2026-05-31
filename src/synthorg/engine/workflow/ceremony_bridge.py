"""Ceremony-to-meeting bridge -- pure conversion functions.

Converts ``SprintCeremonyConfig`` instances into ``MeetingTypeConfig``
instances that the ``MeetingScheduler`` can execute.
"""

from typing import TYPE_CHECKING

from synthorg.communication.meeting.config import (
    MeetingProtocolConfig,
    MeetingTypeConfig,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import SPRINT_CEREMONY_BRIDGE_CREATED

if TYPE_CHECKING:
    from synthorg.engine.workflow.sprint_config import SprintCeremonyConfig

logger = get_logger(__name__)


def build_trigger_event_name(
    ceremony_name: str,
    sprint_id: str,
) -> str:
    """Construct a deterministic event name for trigger-based dispatch.

    Args:
        ceremony_name: The ceremony identifier.
        sprint_id: The sprint identifier.

    Returns:
        Event name like ``"ceremony.daily_standup.sprint-1"``.
    """
    return f"ceremony.{ceremony_name}.{sprint_id}"


def ceremony_to_meeting_type(
    ceremony: SprintCeremonyConfig,
    sprint_id: str,
) -> MeetingTypeConfig:
    """Bridge a SprintCeremonyConfig to a MeetingTypeConfig.

    Conversion rules:

    - **Frequency-based** (ceremony has ``frequency``): creates a
      frequency-based ``MeetingTypeConfig``.
    - **Trigger-only** (ceremony has ``policy_override`` but no
      ``frequency``): creates a trigger-based ``MeetingTypeConfig``
      with a deterministic event name.

    For hybrid ceremonies (both ``frequency`` and ``policy_override``),
    the frequency path is used here.  The task-driven trigger path is
    handled separately by the ``CeremonyScheduler`` calling
    ``MeetingScheduler.trigger_event()``.

    Args:
        ceremony: The sprint ceremony configuration.
        sprint_id: The active sprint ID (used for trigger event names).

    Returns:
        A ``MeetingTypeConfig`` compatible with ``MeetingScheduler``.
    """
    protocol_config = MeetingProtocolConfig(protocol=ceremony.protocol)

    if ceremony.frequency is not None:
        # Frequency-based (or hybrid -- frequency path).
        meeting_type = MeetingTypeConfig(
            name=ceremony.name,
            frequency=ceremony.frequency,
            participants=ceremony.participants,
            duration_tokens=ceremony.duration_tokens,
            protocol_config=protocol_config,
        )
    else:
        # Trigger-only.
        event_name = build_trigger_event_name(ceremony.name, sprint_id)
        meeting_type = MeetingTypeConfig(
            name=ceremony.name,
            trigger=event_name,
            participants=ceremony.participants,
            duration_tokens=ceremony.duration_tokens,
            protocol_config=protocol_config,
        )

    logger.info(
        SPRINT_CEREMONY_BRIDGE_CREATED,
        ceremony=ceremony.name,
        sprint_id=sprint_id,
        has_frequency=ceremony.frequency is not None,
        has_trigger=meeting_type.trigger is not None,
        has_policy_override=ceremony.policy_override is not None,
    )
    return meeting_type
