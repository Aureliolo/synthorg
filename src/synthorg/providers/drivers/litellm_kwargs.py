# module-kind: declarative
"""Typed keyword-argument assembly for the LiteLLM ``acompletion`` call.

Isolates the anti-corruption mapping between SynthOrg's
``CompletionConfig`` and litellm's per-parameter ``acompletion``
signature so ``litellm_driver`` stays focused on dispatch and response
mapping.
"""

from typing import TypedDict

from synthorg.providers.models import CompletionConfig


class _AcompletionRequiredKwargs(TypedDict):
    """Keyword arguments the driver always sets on every ``acompletion`` call."""

    model: str
    messages: list[dict[str, object]]


class _AcompletionKwargs(_AcompletionRequiredKwargs, total=False):
    """Typed view of the keyword arguments handed to ``litellm.acompletion``.

    litellm types ``acompletion`` with precise per-parameter names, so
    passing an opaque ``dict[str, object]`` would not type-check at the
    call site.  This TypedDict enumerates exactly the parameters the
    driver sets: ``model`` and ``messages`` are always present (inherited
    from ``_AcompletionRequiredKwargs``); the rest are optional. Splatting
    it matches each key to the corresponding ``acompletion`` parameter by
    name.
    """

    tools: list[dict[str, object]]
    stream: bool
    stream_options: dict[str, bool]
    api_key: str
    extra_headers: dict[str, str]
    api_base: str
    temperature: float
    max_tokens: int
    stop: list[str]
    top_p: float
    timeout: float


def _apply_completion_config(
    kwargs: _AcompletionKwargs,
    config: CompletionConfig | None,
) -> _AcompletionKwargs:
    """Return a new kwargs mapping with ``CompletionConfig`` fields merged in.

    Returns:
        A copy of ``kwargs`` with the config fields applied; the input
        mapping is left unmodified.
    """
    merged = kwargs.copy()
    if config is None:
        return merged
    if config.temperature is not None:
        merged["temperature"] = config.temperature
    if config.max_tokens is not None:
        merged["max_tokens"] = config.max_tokens
    if config.stop_sequences:
        merged["stop"] = list(config.stop_sequences)
    if config.top_p is not None:
        merged["top_p"] = config.top_p
    if config.timeout is not None:
        merged["timeout"] = config.timeout
    return merged
