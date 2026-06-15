"""Shared agent-persona system-prompt renderer.

A single place that turns an :class:`AgentIdentity` into a compact
``system`` prompt (role, department, seniority, personality) with the
canonical ``untrusted_content_directive`` appended. Used by the meeting
agent caller, the concern-routed proposer responder, and the
multi-agent group chat so every persona-driven LLM call fences
untrusted content identically.
"""

from collections.abc import Sequence

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import flatten_label
from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    TAG_TASK_DATA,
    untrusted_content_directive,
)

# The fences a persona-driven turn may receive: agenda / history /
# human content in ``<task-data>`` and upstream agent turns in
# ``<peer-contribution>``. Callers that emit no peer turns still
# advertise the directive so a single renderer serves every surface.
_DEFAULT_PERSONA_FENCES: tuple[str, ...] = (TAG_TASK_DATA, TAG_PEER_CONTRIBUTION)


def render_agent_persona_body(identity: AgentIdentity) -> str:
    """Render the identity preamble for *identity* without any directive.

    The role + department + seniority + personality lines that put the
    model in character, with no untrusted-content directive appended.
    Callers that already emit their own directive (the concern-routed
    proposer, whose prompt template appends one) inject this preamble as
    the identity line; callers building a standalone ``system`` prompt
    use :func:`render_agent_system_prompt` instead.

    Args:
        identity: The agent whose persona drives the turn.

    Returns:
        The multi-line persona preamble (no trailing directive).
    """
    # Flatten every interpolated identity field: name / role / department
    # reach AgentIdentity from semi-trusted HiringRequest / CandidateCard
    # values, and personality fields likewise. Flattening here stops a
    # crafted value (e.g. a newline-injected "Ignore all prior
    # instructions") from forging a fresh instruction line in this SYSTEM
    # preamble.
    name = flatten_label(identity.name)
    role = flatten_label(identity.role)
    department = flatten_label(identity.department)
    lines: list[str] = [
        f"You are {name}, a {role} in the {department} department.",
        f"Seniority level: {identity.level.value}.",
    ]
    traits = tuple(flatten_label(t) for t in identity.personality.traits)
    if traits:
        lines.append("Personality traits: " + ", ".join(traits) + ".")
    communication_style = flatten_label(identity.personality.communication_style)
    if communication_style:
        lines.append(f"Communication style: {communication_style}.")
    return "\n".join(lines)


def render_agent_system_prompt(
    identity: AgentIdentity,
    *,
    fences: Sequence[str] = _DEFAULT_PERSONA_FENCES,
) -> str:
    """Render a persona ``system`` prompt for *identity*.

    Builds the role + department + seniority + personality preamble and
    appends the ``untrusted_content_directive`` for *fences* so the
    model treats fenced content as data, not instructions. Protocols
    inject the full turn context (agenda, prior contributions, lens)
    into the ``user`` message, so the system prompt only carries
    agent-stable identity.

    Args:
        identity: The agent whose persona drives the turn.
        fences: The untrusted-content fence tags the caller may emit.
            Defaults to ``<task-data>`` + ``<peer-contribution>``.

    Returns:
        The rendered system prompt, including the untrusted-content
        directive.
    """
    body = render_agent_persona_body(identity)
    directive = untrusted_content_directive(tuple(fences))
    return f"{body}\n\n{directive}"


__all__ = ["render_agent_persona_body", "render_agent_system_prompt"]
