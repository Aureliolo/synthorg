"""Shared agent-persona system-prompt renderer.

A single place that turns an :class:`AgentIdentity` into a compact
``system`` prompt (name, role, department) carrying the house-style
directives in scope for the agent and the canonical
``untrusted_content_directive``. Used by the planning, evaluation, retro and
plan-review sessions, the multi-agent conversation caller, the concern-routed
proposer responder, and the group chat, so every persona-driven LLM call
fences untrusted content identically and is told the style rules its output
is judged against.
"""

from collections.abc import Sequence
from typing import Final

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import flatten_label
from synthorg.engine.output_style.house_style import build_house_style_section
from synthorg.engine.prompt_providers import current_prompt_providers
from synthorg.engine.prompt_safety import (
    TAG_PEER_CONTRIBUTION,
    TAG_TASK_DATA,
    untrusted_content_directive,
)
from synthorg.observability import get_logger
from synthorg.observability.events.output_style import OUTPUT_STYLE_PROMPT_SKIPPED

logger = get_logger(__name__)

# The fences a persona-driven turn may receive: agenda / history /
# human content in ``<task-data>`` and upstream agent turns in
# ``<peer-contribution>``. Callers that emit no peer turns still
# advertise the directive so a single renderer serves every surface.
_DEFAULT_PERSONA_FENCES: Final[tuple[str, ...]] = (
    TAG_TASK_DATA,
    TAG_PEER_CONTRIBUTION,
)

#: Word-for-word the distinction the full prompt template draws, because the
#: two are the same claim made to the same agent and only one of them can be
#: right. Naming the em-dash ban specifically is what keeps the claim true for
#: every session this renderer serves: the retro and plan-review submit tools
#: run no style guard at all, so a blanket "anything here is rejected" would
#: promise those sessions a consequence their own output path does not have.
_HOUSE_STYLE_HEADING: Final[str] = (
    "House writing style. Write in it on everything you produce. The em-dash "
    "ban is hard-enforced: output containing an em-dash (U+2014) is rejected "
    "where you submit it and returned to you to rewrite, so never emit one. "
    "The remaining directives are expected and monitored."
)


def render_agent_persona_body(identity: AgentIdentity) -> str:
    """Render the identity preamble for *identity* without any directive.

    The name + role + department line that puts the model in character,
    with no untrusted-content directive appended. Callers that already
    emit their own directive (the concern-routed proposer, whose prompt
    template appends one) inject this preamble as the identity line;
    callers building a standalone ``system`` prompt use
    :func:`render_agent_system_prompt` instead.

    Args:
        identity: The agent whose persona drives the turn.

    Returns:
        The persona preamble (no trailing directive).
    """
    # Flatten every interpolated identity field: name / role / department
    # reach AgentIdentity from semi-trusted HiringRequest / CandidateCard
    # values. Flattening here stops a crafted value (e.g. a
    # newline-injected "Ignore all prior instructions") from forging a
    # fresh instruction line in this SYSTEM preamble.
    name = flatten_label(identity.name)
    role = flatten_label(identity.role)
    department = flatten_label(identity.department)
    return f"You are {name}, a {role} in the {department} department."


def _house_style_block(identity: AgentIdentity) -> str:
    """Render the in-scope house-style directives for *identity*.

    The full prompt build injects the same directives from the same ambient
    provider; this is the compact persona prompt's half of it. Without it a
    session is judged at its delivery boundary against rules it was never
    given, and discovers them one rejection at a time: a recorded planning run
    spent 21 of its turns being handed back an em-dash it had no way to know
    was banned, then gave up.

    Taken through :func:`current_prompt_providers`, the declared single reader
    of the ambient providers, rather than off the global directly: this build
    needs only the one, but a second reader is how the two layers come to
    disagree about which snapshot a prompt was built from. Resolving it once
    here also keeps the ``None`` check and the directive read on the same
    provider instance, which a hot-swap between two reads would not.

    Args:
        identity: The agent whose persona drives the turn.

    Returns:
        The house-style section, or an empty string when no provider is bound
        or none of its directives are in scope for the agent.
    """
    provider = current_prompt_providers().house_style
    if provider is None:
        # Worth a line, quiet: a prompt built before the boot hook binds the
        # provider carries no style at all, and the guard at the far end of
        # that session does not care that the agent was never told.
        logger.debug(
            OUTPUT_STYLE_PROMPT_SKIPPED,
            agent_role=identity.role,
            agent_department=identity.department,
            reason="no_provider_bound",
        )
        return ""
    section = build_house_style_section(
        provider.list_directives(role=identity.role, department=identity.department)
    )
    if not section:
        logger.debug(
            OUTPUT_STYLE_PROMPT_SKIPPED,
            agent_role=identity.role,
            agent_department=identity.department,
            reason="no_directives_in_scope",
        )
        return ""
    return f"{_HOUSE_STYLE_HEADING}\n{section}"


def render_agent_system_prompt(
    identity: AgentIdentity,
    *,
    fences: Sequence[str] = _DEFAULT_PERSONA_FENCES,
) -> str:
    """Render a persona ``system`` prompt for *identity*.

    Builds the name + role + department preamble, the house-style
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
