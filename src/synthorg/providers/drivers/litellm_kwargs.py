"""Typed keyword-argument assembly for the LiteLLM ``acompletion`` call.

Isolates the anti-corruption mapping between SynthOrg's
``CompletionConfig`` and litellm's per-parameter ``acompletion``
signature so ``litellm_driver`` stays focused on dispatch and response
mapping.
"""

from typing import TypedDict

from synthorg.providers.models import CompletionConfig


class _AcompletionKwargs(TypedDict, total=False):
    """Typed view of the keyword arguments handed to ``litellm.acompletion``.

    litellm types ``acompletion`` with precise per-parameter signatures,
    so an opaque ``dict[str, object]`` cannot be splatted into it. This
    ``total=False`` TypedDict enumerates exactly the parameters the driver
    sets; splatting it matches each key to the corresponding ``acompletion``
    parameter by name (``api_base`` flows through litellm's own ``**kwargs``).
    """

    model: str
    messages: list[dict[str, object]]
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
    """Merge ``CompletionConfig`` fields into ``kwargs`` in place.

    Returns:
        The same ``kwargs`` mapping, with config fields applied.
    """
    if config is None:
        return kwargs
    if config.temperature is not None:
        kwargs["temperature"] = config.temperature
    if config.max_tokens is not None:
        kwargs["max_tokens"] = config.max_tokens
    if config.stop_sequences:
        kwargs["stop"] = list(config.stop_sequences)
    if config.top_p is not None:
        kwargs["top_p"] = config.top_p
    if config.timeout is not None:
        kwargs["timeout"] = config.timeout
    return kwargs
