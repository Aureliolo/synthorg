# module-kind: code
"""Prompt builders for the structured-phases meeting protocol.

Pure string builders extracted from ``structured_phases.py``. Each
participant-supplied input is wrapped in its own ``<peer-contribution>``
fence so a literal closing tag cannot inject instructions into a leader
or peer turn.
"""

from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    wrap_untrusted,
)


def build_input_prompt(agenda_text: str, agent_id: str) -> str:
    """Build an input-gathering prompt for an agent.

    Args:
        agenda_text: Formatted agenda prompt text.
        agent_id: The participant being prompted.

    Returns:
        The prompt asking the agent for input on each agenda item.
    """
    return (
        f"{agenda_text}\n\n"
        f"{agent_id}, please provide your input on each agenda item. "
        f"Share your perspective, concerns, and recommendations."
    )


def build_conflict_check_prompt(
    agenda_text: str,
    inputs: list[tuple[str, str]],
) -> str:
    """Build a prompt for the leader to check for conflicts.

    Each participant input is wrapped in its own ``<peer-contribution>``
    fence so a literal closing tag in any input cannot inject
    instructions into the leader's reasoning.

    Args:
        agenda_text: Formatted agenda prompt text.
        inputs: ``(agent_id, content)`` pairs to review.

    Returns:
        The conflict-check prompt embedding every fenced input.
    """
    parts = [agenda_text, "", "Participant inputs:"]
    for agent_id, content in inputs:
        parts.append(f"\n--- {agent_id} ---")
        parts.append(wrap_untrusted(TAG_PEER_CONTRIBUTION, content))
    parts.append("")
    parts.append(
        "As the meeting leader, review the inputs above. "
        "Are there any conflicts or disagreements between participants? "
        "Reply with 'CONFLICTS: YES' or 'CONFLICTS: NO' on the first "
        "line, followed by your analysis."
    )
    return "\n".join(parts)


def build_discussion_prompt(
    agenda_text: str,
    inputs: list[tuple[str, str]],
    conflict_analysis: str,
    agent_id: str,
) -> str:
    """Build a discussion prompt for a participant.

    Inputs and the conflict analysis (the leader's prior turn output,
    which may itself contain prompt-injected content from upstream
    inputs) are wrapped in ``<peer-contribution>`` fences so a
    compromised upstream turn cannot escape and inject into this
    participant's reasoning.

    Args:
        agenda_text: Formatted agenda prompt text.
        inputs: ``(agent_id, content)`` pairs from input gathering.
        conflict_analysis: The leader's conflict-check output.
        agent_id: The participant being prompted.

    Returns:
        The discussion prompt for the named participant.
    """
    parts = [agenda_text, "", "Previous inputs:"]
    for aid, content in inputs:
        parts.append(f"\n--- {aid} ---")
        parts.append(wrap_untrusted(TAG_PEER_CONTRIBUTION, content))
    parts.append(
        "\nConflict analysis:\n"
        + wrap_untrusted(TAG_PEER_CONTRIBUTION, conflict_analysis),
    )
    parts.append("")
    parts.append(
        f"{agent_id}, please respond to the conflicts identified. "
        f"Provide your counter-arguments or revised position."
    )
    return "\n".join(parts)


def build_synthesis_prompt(
    agenda_text: str,
    inputs: list[tuple[str, str]],
    discussion: list[tuple[str, str]] | None = None,
) -> str:
    """Build a synthesis prompt for the leader.

    Each input and discussion contribution is wrapped in its own
    ``<peer-contribution>`` fence so a compromised participant cannot
    inject into the synthesis decision.

    Args:
        agenda_text: Formatted agenda prompt text.
        inputs: ``(agent_id, content)`` pairs from input gathering.
        discussion: Optional ``(agent_id, content)`` discussion pairs.

    Returns:
        The synthesis prompt embedding every fenced input and
        discussion contribution.
    """
    parts = [agenda_text, "", "Participant inputs:"]
    for agent_id, content in inputs:
        parts.append(f"\n--- {agent_id} ---")
        parts.append(wrap_untrusted(TAG_PEER_CONTRIBUTION, content))
    if discussion:
        parts.append("\nDiscussion contributions:")
        for agent_id, content in discussion:
            parts.append(f"\n--- {agent_id} ---")
            parts.append(wrap_untrusted(TAG_PEER_CONTRIBUTION, content))
    parts.append("")
    parts.append(
        "As the meeting leader, synthesize all inputs and discussion "
        "into your output using exactly these section headers:\n\n"
        "Decisions:\n"
        "1. <decision>\n\n"
        "Action Items:\n"
        "- <action item> (assigned to <agent_id>)"
    )
    return "\n".join(parts)
