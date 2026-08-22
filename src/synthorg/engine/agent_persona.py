"""Shared agent-persona system-prompt renderer.

A single place that turns an :class:`AgentIdentity` into a compact
``system`` prompt (role, department, personality) carrying the house-style
directives in scope for the agent and the canonical
``untrusted_content_directive``. Used by the planning, evaluation, retro and
plan-review sessions, the meeting agent caller, the concern-routed proposer
responder, and the multi-agent group chat, so every persona-driven LLM call
fences untrusted content identically and is told the style rules its output
is judged against.
"""

from collections.abc import Sequence
from typing import Final

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import flatten_label
from synthorg.engine.output_style.house_style import build_house_style_section
from synthorg.engine.output_style.provider import current_house_style_provider
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

#: Names the consequence, not just the preference. Every session this prompt
#: drives delivers through a boundary that rejects a style violation and hands
#: it back, so the agent that knows the rule spends its turns on the work.
_HOUSE_STYLE_HEADING: Final[str] = (
    "House writing style. These apply to what you deliver: output that "
    "breaks one is rejected and handed back to you to fix."
)


def render_agent_persona_body(identity: AgentIdentity) -> str:
    """Render the identity preamble for *identity* without any directive.

    The role + department + personality lines that put the
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
    ]
    traits = tuple(flatten_label(t) for t in identity.personality.traits)
    if traits:
        lines.append("Personality traits: " + ", ".join(traits) + ".")
    communication_style = flatten_label(identity.personality.communication_style)
    if communication_style:
        lines.append(f"Communication style: {communication_style}.")
    return "\n".join(lines)


def _house_style_block(identity: AgentIdentity) -> str:
    """Render the in-scope house-style directives for *identity*.

    The full prompt build injects the same directives from the same ambient
    provider; this is the compact persona prompt's half of it. Without it a
    session is judged at its delivery boundary against rules it was never
    given, and discovers them one rejection at a time: a recorded planning run
    spent 21 of its turns being handed back an em-dash it had no way to know
    was banned, then gave up.

    Read once here, which is this prompt's whole build, so a hot-swap cannot
    land between the directives and the heading that introduces them.

    Args:
        identity: The agent whose persona drives the turn.

    Returns:
        The house-style section, or an empty string when no provider is bound
        or none of its directives are in scope for the agent.
    """
    provider = current_house_style_provider()
    if provider is None:
        return ""
    section = build_house_style_section(
        provider.list_directives(role=identity.role, department=identity.department)
    )
    if not section:
        return ""
    return f"{_HOUSE_STYLE_HEADING}\n{section}"


def render_agent_system_prompt(
    identity: AgentIdentity,
    *,
    fences: Sequence[str] = _DEFAULT_PERSONA_FENCES,
) -> str:
    """Render a persona ``system`` prompt for *identity*.

    Builds the role + department + personality preamble, the house-style
    directives in scope for the agent, and the ``untrusted_content_directive``
    for *fences* so the model treats fenced content as data, not instructions.
    Protocols inject the full turn context (agenda, prior contributions, lens)
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
    sections = (
        render_agent_persona_body(identity),
        _house_style_block(identity),
        untrusted_content_directive(tuple(fences)),
    )
    return "\n\n".join(section for section in sections if section)


__all__ = ["render_agent_persona_body", "render_agent_system_prompt"]
