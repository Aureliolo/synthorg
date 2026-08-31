# module-kind: code
"""The sampling one agent's binding implies, in one place.

An agent binds a ``(provider, model)`` pair, and how that model should be
sampled is a property of the model rather than of the session type: a vendor
publishes a temperature and a nucleus threshold together, and some families
publish a reasoning depth instead and ignore sampling entirely. So the binding
carries all of it, and every dispatch path resolves through here rather than
building its own config from whichever half of the binding it happens to hold.

Resolution is a MERGE, not a choice between the caller's config and the
binding. A caller that states one dial has not thereby stated the rest, so
choosing would discard a whole binding on the strength of a single stated
field: a session that only cared about a token ceiling would silently lose the
reasoning depth an operator bound. What the caller states wins field by field,
and the binding answers for everything left open. One resolver over an ordered
precedence, rather than two authorities racing.

What a caller stated is read from Pydantic's own ``model_fields_set``, because
two of these dials cannot be told apart from their defaults by value:
``CompletionConfig.top_p`` defaults to ``1.0`` and is a legitimate thing to ask
for explicitly.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.providers.models import CompletionConfig


def resolve_sampling(
    identity: AgentIdentity,
    requested: CompletionConfig | None = None,
) -> CompletionConfig:
    """Resolve the completion config *identity* should be dispatched with.

    A dial the binding leaves unset is omitted rather than defaulted, so
    :class:`CompletionConfig`'s own default stands instead of a copy of it that
    a later change to that default would silently leave behind. That omission
    is also what keeps ``model_copy`` safe here: ``top_p``, ``max_tokens`` and
    ``reasoning_effort`` are optional on the binding and non-optional or
    differently-bounded on the config, and every value that does pass through
    carries the same bound on both sides (temperature 0-2, ``top_p`` 0-1,
    ``max_tokens`` positive), so nothing needs revalidating.

    Args:
        identity: The agent being dispatched.
        requested: What the caller asked for, if anything. Every field it
            states survives untouched.

    Returns:
        The caller's stated fields over the binding's own. A dial neither
        states stays unset, falling through to whichever ladder owns it.
    """
    model = identity.model
    dials: dict[str, object] = {"temperature": model.temperature}
    if model.top_p is not None:
        dials["top_p"] = model.top_p
    if model.max_tokens is not None:
        dials["max_tokens"] = model.max_tokens
    if model.reasoning_effort is not None:
        dials["reasoning_effort"] = model.reasoning_effort
    if requested is None:
        return CompletionConfig(temperature=model.temperature).model_copy(update=dials)
    unstated = {
        name: value
        for name, value in dials.items()
        if name not in requested.model_fields_set
    }
    return requested.model_copy(update=unstated) if unstated else requested


__all__ = ["resolve_sampling"]
