# module-kind: code
"""The sampling one agent's binding implies, in one place.

An agent binds a ``(provider, model)`` pair, and how that model should be
sampled is a property of the model rather than of the session type: a vendor
publishes a temperature and a nucleus threshold together, and some families
publish a reasoning depth instead and ignore sampling entirely. So the binding
carries all of it and every dispatch path reads it from here.

The alternative shipped for a while and is what this replaces: each dispatch
path built its own :class:`CompletionConfig` from whatever it had to hand, so a
work session sampled at the agent's own temperature while a planning session
sampled at a strategy-level default nobody had matched to a model. Two owners
for one fact, and the quieter one won wherever it happened to be read.

A caller that supplies its own config still wins whole. This answers only for
the case where nobody upstream expressed a preference, which is every ordinary
dispatch.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.providers.models import CompletionConfig


def binding_sampling(identity: AgentIdentity) -> CompletionConfig:
    """Build the completion config *identity*'s own binding declares.

    ``top_p`` is omitted rather than defaulted when the binding states none, so
    :class:`CompletionConfig`'s own default stands instead of a copy of it that
    a later change to that default would silently leave behind.

    Args:
        identity: The agent being dispatched.

    Returns:
        A config carrying the binding's sampling, reasoning depth and response
        ceiling. Unset fields stay unset, so each falls through to whichever
        ladder owns it.
    """
    model = identity.model
    if model.top_p is None:
        return CompletionConfig(
            temperature=model.temperature,
            max_tokens=model.max_tokens,
            reasoning_effort=model.reasoning_effort,
        )
    return CompletionConfig(
        temperature=model.temperature,
        top_p=model.top_p,
        max_tokens=model.max_tokens,
        reasoning_effort=model.reasoning_effort,
    )


__all__ = ["binding_sampling"]
