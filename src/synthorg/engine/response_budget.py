# module-kind: code
"""One owner for how many tokens an agent may spend on a single response.

The question has two legitimate authorities, so it gets an ordered ladder and
exactly one resolver: the agent's own ``max_tokens`` when an operator set one,
and ``engine.agent_max_response_tokens`` otherwise. ``None`` on the binding is
what tells those apart, which is why the field is optional rather than carrying
a flat default that no caller ever overrode.
"""

from typing import Final

from synthorg.core.agent import AgentIdentity
from synthorg.settings.kill_switch import resolve_int_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

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
        else the shipped default.
    """
    own = identity.model.max_tokens
    if own is not None:
        return own
    return await resolve_int_with_fallback(
        resolver=resolver,
        namespace=_ENGINE_NAMESPACE,
        key=_RESPONSE_TOKENS_KEY,
        fallback=DEFAULT_AGENT_MAX_RESPONSE_TOKENS,
    )


__all__ = ["DEFAULT_AGENT_MAX_RESPONSE_TOKENS", "resolve_response_tokens"]
