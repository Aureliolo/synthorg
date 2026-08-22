# module-kind: code
"""One owner for how many tokens an agent's own dispatch may spend on a reply.

The question has two legitimate authorities, so it gets an ordered ladder and
exactly one resolver: the agent's own ``max_tokens`` when an operator set one,
and ``engine.agent_max_response_tokens`` otherwise. ``None`` on the binding is
what tells those apart, which is why the field is optional rather than carrying
a flat default that no caller ever overrode.

A meeting turn is deliberately outside this ladder and stays with its own owner
in :mod:`synthorg.communication.meeting.agent_caller`: there the MEETING states
the cap, because the turn is one contribution to a bounded conversation rather
than an agent working, and the agent's own binding only tightens it further.
"""

from typing import Final

from synthorg.core.agent import AgentIdentity
from synthorg.observability import get_logger
from synthorg.observability.events.execution import EXECUTION_RESPONSE_CEILING_REFUSED
from synthorg.settings.kill_switch import resolve_int_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

_ENGINE_NAMESPACE: Final[str] = "engine"
_RESPONSE_TOKENS_KEY: Final[str] = "agent_max_response_tokens"

#: Mirrors the setting definition's own default so the resolver-up and
#: resolver-down paths agree, which is what the shared helper asks callers for.
DEFAULT_AGENT_MAX_RESPONSE_TOKENS: Final[int] = 32_768


async def resolve_response_tokens(
    resolver: ConfigResolverProtocol | None, identity: AgentIdentity
) -> int:
    """Read the output ceiling for one response by *identity*.

    Read per dispatch rather than captured once, so an operator raising the
    ceiling applies to the next run instead of the next restart.

    The ceiling matters more than it looks: a reasoning model spends this
    budget on hidden reasoning BEFORE it can emit content or a tool call, so a
    value sized for a model that answers directly produces sessions that think
    until truncated, return no tool call, and are recorded as finished work,
    because a turn with no tool call is how a session reports completion. A
    ceiling costs nothing when unused, so a low one buys no saving.

    Args:
        resolver: The wired settings resolver, or ``None``.
        identity: The agent whose binding may already answer.

    Returns:
        The agent's own ceiling when it set one, else the configured value,
        else the shipped default. Always a positive token count.
    """
    own = identity.model.max_tokens
    if own is None:
        own = await resolve_int_with_fallback(
            resolver=resolver,
            namespace=_ENGINE_NAMESPACE,
            key=_RESPONSE_TOKENS_KEY,
            fallback=DEFAULT_AGENT_MAX_RESPONSE_TOKENS,
        )
    return _positive(own, agent_id=str(identity.id))


def _positive(resolved: int, *, agent_id: str) -> int:
    """Hold the ladder's answer to the one property every consumer assumes.

    Each of the three sources is constrained positive where it is written (the
    binding's ``gt=0``, the setting's ``min_value``, the default above), and
    NONE of them is checked again where the value is used. A stored value can
    outlive the constraint that admitted it, and a zero here does not fail: it
    reaches the driver as a request for no output at all, which reads as a
    model that answered nothing.

    Args:
        resolved: What the ladder answered.
        agent_id: Whose dispatch it was resolved for.

    Returns:
        *resolved* when it is a usable ceiling, else the shipped default.
    """
    if resolved > 0:
        return resolved
    logger.warning(
        EXECUTION_RESPONSE_CEILING_REFUSED,
        agent_id=agent_id,
        resolved=resolved,
        applied=DEFAULT_AGENT_MAX_RESPONSE_TOKENS,
    )
    return DEFAULT_AGENT_MAX_RESPONSE_TOKENS


__all__ = ["DEFAULT_AGENT_MAX_RESPONSE_TOKENS", "resolve_response_tokens"]
