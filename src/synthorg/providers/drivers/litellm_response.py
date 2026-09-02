# module-kind: adapter
"""LiteLLM response-to-domain mapping.

Splits the ``ModelResponse`` -> :class:`CompletionResponse` mapping out
of the driver body so the driver stays within its size budget. Pure over
its inputs; the provider name is threaded in for the empty-choices log
and error context.
"""

from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_CALL_ERROR
from synthorg.providers import errors
from synthorg.providers._cost import token_usage_from_response_usage
from synthorg.providers.drivers.mappers import (
    extract_reasoning,
    extract_tool_calls,
    map_finish_reason,
    normalize_empty_finish,
)
from synthorg.providers.models import CompletionResponse

logger = get_logger(__name__)


def map_response(
    response: object,
    model_config: ProviderModelConfig,
    *,
    provider_name: str,
) -> CompletionResponse:
    """Map a LiteLLM ``ModelResponse`` to ``CompletionResponse``.

    Returns:
        A ``CompletionResponse`` populated from the first choice's
        content, tool calls, finish reason, token usage, and cost.

    Raises:
        ProviderInternalError: If the LiteLLM response has no choices.
    """
    choices = getattr(response, "choices", [])
    if not choices:
        logger.error(
            PROVIDER_CALL_ERROR,
            provider=provider_name,
            model=model_config.id,
            error="empty_choices_in_response",
        )
        msg = f"Provider returned empty choices for model {model_config.id!r}"
        raise errors.ProviderInternalError(
            msg, context={"provider": provider_name, "model": model_config.id}
        )

    choice = choices[0]
    message = choice.message

    content: str | None = getattr(message, "content", None)
    reasoning = extract_reasoning(message)
    # Materialised once so the count below cannot re-consume a one-shot
    # iterable, and so it is a length rather than whatever the provider
    # object happens to support.
    raw_tool_calls: list[object] = list(getattr(message, "tool_calls", None) or ())
    tool_calls = extract_tool_calls(raw_tool_calls)
    finish = normalize_empty_finish(
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls,
        finish=map_finish_reason(getattr(choice, "finish_reason", None)),
        provider=provider_name,
        model=model_config.id,
        had_raw_tool_calls=bool(raw_tool_calls),
    )

    raw_usage = getattr(response, "usage", None)
    usage = token_usage_from_response_usage(
        raw_usage,
        cost_per_1k_input=model_config.cost_per_1k_input,
        cost_per_1k_output=model_config.cost_per_1k_output,
    )
    return CompletionResponse(
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls,
        # Fewer survived than arrived. Counted rather than tested for an empty
        # result because extraction drops one call at a time: a turn asking for
        # two tools can deliver one and lose the other, and reading the loss
        # off an empty ``tool_calls`` reports that turn as having lost nothing.
        dropped_tool_calls=len(tool_calls) < len(raw_tool_calls),
        finish_reason=finish,
        usage=usage,
        model=model_config.id,
        provider_request_id=getattr(response, "id", None) or None,
    )
