"""Shared prompt builders for meeting protocol implementations."""

from collections.abc import Mapping  # noqa: TC003
from typing import TYPE_CHECKING

from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.communication.meeting.models import MeetingAgenda

logger = get_logger(__name__)


def build_agenda_prompt(agenda: MeetingAgenda) -> str:
    """Build the initial agenda prompt text.

    Agenda fields (title, context, item title/description, presenter_id)
    originate from API request bodies and are attacker-controllable.
    They are wrapped in a single SEC-1 ``<task-data>`` fence so a literal
    closing tag in any field cannot break out and inject instructions
    into downstream meeting agents (#1596).

    Args:
        agenda: The meeting agenda to format.

    Returns:
        Formatted agenda text with all attacker-controllable values
        fenced.  The literal ``Meeting agenda:`` header sits outside
        the ``<task-data>`` fence as model-trusted prose so the LLM
        can still read it as instructions; every user-supplied agenda
        detail (title, context, items) goes inside the single fence.
    """
    inner: list[str] = [f"Title: {agenda.title}"]
    if agenda.context:
        inner.append(f"Context: {agenda.context}")
    if agenda.items:
        inner.append("Agenda items:")
        for i, item in enumerate(agenda.items, 1):
            entry = f"  {i}. {item.title}"
            if item.description:
                entry += f" -- {item.description}"
            if item.presenter_id:
                entry += f" (presenter: {item.presenter_id})"
            inner.append(entry)
    return "Meeting agenda:\n" + wrap_untrusted(
        TAG_TASK_DATA,
        "\n".join(inner),
    )


def inject_lens_perspective(
    prompt: str,
    agent_id: str,
    lens_assignments: Mapping[str, str] | None,
) -> str:
    """Append lens perspective instructions to a prompt.

    If the agent has a lens assignment, the lens name is appended
    as a perspective instruction.  Otherwise the prompt is returned
    unchanged.

    SEC-1: ``lens_name`` is interpolated unwrapped because
    ``lens_assignments`` originates from operator-controlled
    strategy config (e.g. ``MeetingsConfig.lens_assignments``), not
    from API request bodies or other agent output.  If a future change
    routes user-supplied values into ``lens_assignments``, wrap
    ``lens_name`` via ``wrap_untrusted(TAG_CONFIG_VALUE, ...)`` and
    extend the meeting agent ``untrusted_content_directive`` to
    include the new tag.

    Args:
        prompt: The base prompt text.
        agent_id: The agent to look up in assignments.
        lens_assignments: Optional mapping of agent ID to lens name.

    Returns:
        The prompt, optionally extended with lens instructions.
    """
    if not lens_assignments or agent_id not in lens_assignments:
        return prompt
    lens_name = lens_assignments[agent_id]
    return (
        f"{prompt}\n\n"
        f"[Strategic Lens: {lens_name}]\n"
        f"Adopt the {lens_name} perspective in your analysis. "
        f"Evaluate the agenda items through this specific lens."
    )
