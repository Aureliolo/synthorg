"""Ceremony-to-meeting bridge -- pure conversion functions.

Converts ``SprintCeremonyConfig`` instances into ``MeetingTypeConfig``
instances that the ``MeetingScheduler`` can execute.
"""

from synthorg.communication.meeting.config import MeetingTypeConfig
from synthorg.engine.workflow.sprint_config import SprintCeremonyConfig
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import SPRINT_CEREMONY_BRIDGE_CREATED

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

    Always trigger-based, on the deterministic event name the
    ``CeremonyScheduler`` dispatches.  The ceremony scheduler owns
    *when* a ceremony fires, including the calendar strategy's reading
    of ``ceremony.frequency``; a frequency-based meeting type would add
    a periodic task firing the same ceremony a second time.

    Args:
        ceremony: The sprint ceremony configuration.
        sprint_id: The active sprint ID (used for trigger event names).

    Returns:
        A ``MeetingTypeConfig`` compatible with ``MeetingScheduler``.
    """
    meeting_type = MeetingTypeConfig(
        name=ceremony.name,
        trigger=build_trigger_event_name(ceremony.name, sprint_id),
        participants=ceremony.participants,
        duration_tokens=ceremony.duration_tokens,
        protocol_config=ceremony.protocol_config,
    )
    logger.info(
        SPRINT_CEREMONY_BRIDGE_CREATED,
        ceremony=ceremony.name,
        sprint_id=sprint_id,
        protocol=ceremony.protocol_config.protocol.value,
        has_frequency=ceremony.frequency is not None,
        has_policy_override=ceremony.policy_override is not None,
    )
    return meeting_type
