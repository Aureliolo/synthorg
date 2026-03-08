"""Shared prompt builders for meeting protocol implementations."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_company.communication.meeting.models import MeetingAgenda


def build_agenda_prompt(agenda: MeetingAgenda) -> str:
    """Build the initial agenda prompt text.

    Args:
        agenda: The meeting agenda to format.

    Returns:
        Formatted agenda text for use in agent prompts.
    """
    parts = [f"Meeting: {agenda.title}"]
    if agenda.context:
        parts.append(f"Context: {agenda.context}")
    if agenda.items:
        parts.append("Agenda items:")
        for i, item in enumerate(agenda.items, 1):
            entry = f"  {i}. {item.title}"
            if item.description:
                entry += f" — {item.description}"
            if item.presenter_id:
                entry += f" (presenter: {item.presenter_id})"
            parts.append(entry)
    return "\n".join(parts)
