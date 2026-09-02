# module-kind: code
"""Token-cost computation for completion providers.

Stateless helper so ``BaseCompletionProvider`` holds only retry /
rate-limit orchestration. Drivers call ``compute_token_cost`` to build a
``TokenUsage`` from raw counts.
"""

import math
from typing import Final

from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger
from synthorg.observability.events.provider import PROVIDER_COST_INVALID

from .errors import InvalidRequestError
from .models import TokenUsage

logger = get_logger(__name__)

# Provider rates are quoted per 1,000 tokens, so raw counts divide by
# this before multiplying by the per-1k rate.
_TOKENS_PER_1K: Final[int] = 1000


def compute_token_cost(
    input_tokens: int,
    output_tokens: int,
    *,
    cost_per_1k_input: float,
    cost_per_1k_output: float,
    cache_read_input_tokens: int = 0,
    cache_write_input_tokens: int = 0,
) -> TokenUsage:
    """Build a ``TokenUsage`` from raw token counts and per-1k rates.

    Args:
        input_tokens: Number of input tokens (must be >= 0).
        output_tokens: Number of output tokens (must be >= 0).
        cost_per_1k_input: Cost per 1,000 input tokens in the configured
            currency (finite and >= 0).
        cost_per_1k_output: Cost per 1,000 output tokens in the configured
            currency (finite and >= 0).
        cache_read_input_tokens: Input tokens the provider served from a
            cached prompt prefix.
        cache_write_input_tokens: Input tokens the provider wrote into its
            prompt cache.

    Returns:
        Populated ``TokenUsage`` with computed cost.

    Raises:
        InvalidRequestError: If any parameter is negative or
            non-finite.
    """
    if input_tokens < 0:
        msg = "input_tokens must be non-negative"
        logger.warning(PROVIDER_COST_INVALID, field="input_tokens", value=input_tokens)
        raise InvalidRequestError(
            msg,
            context={"input_tokens": input_tokens},
        )
    if output_tokens < 0:
        msg = "output_tokens must be non-negative"
        logger.warning(
            PROVIDER_COST_INVALID, field="output_tokens", value=output_tokens
        )
        raise InvalidRequestError(
            msg,
            context={"output_tokens": output_tokens},
        )
    if cost_per_1k_input < 0 or not math.isfinite(cost_per_1k_input):
        msg = "cost_per_1k_input must be a finite non-negative number"
        logger.warning(
            PROVIDER_COST_INVALID, field="cost_per_1k_input", value=cost_per_1k_input
        )
        raise InvalidRequestError(
            msg,
            context={"cost_per_1k_input": cost_per_1k_input},
        )
    if cost_per_1k_output < 0 or not math.isfinite(cost_per_1k_output):
        msg = "cost_per_1k_output must be a finite non-negative number"
        logger.warning(
            PROVIDER_COST_INVALID, field="cost_per_1k_output", value=cost_per_1k_output
        )
        raise InvalidRequestError(
            msg,
            context={"cost_per_1k_output": cost_per_1k_output},
        )
    cost = (input_tokens / _TOKENS_PER_1K) * cost_per_1k_input + (
        output_tokens / _TOKENS_PER_1K
    ) * cost_per_1k_output
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=round(cost, BUDGET_ROUNDING_PRECISION),
        cache_read_input_tokens=cache_read_input_tokens,
        cache_write_input_tokens=cache_write_input_tokens,
    )


def _cache_tokens(usage_obj: object) -> tuple[int, int]:
    """Read the cached-prefix token counts a provider reported, if any.

    Reads ``prompt_tokens_details.cached_tokens`` (the nested shape LiteLLM
    normalises most providers into), falling back to a flat
    ``cache_read_input_tokens``; the write count is the flat
    ``cache_creation_input_tokens``. Absent from every shape is a provider
    that reported no cache data, which reads as zero: a COUNT has no
    "unknown", and the share these feed is computed over input tokens, so a
    provider that never publishes the field contributes nothing to it rather
    than reading as every call missing.

    Returns:
        ``(cache_read_input_tokens, cache_write_input_tokens)``.
    """
    details = getattr(
        usage_obj, "prompt_tokens_details", None
    )  # lint-allow: ghost-attribute-read -- litellm usage object
    read = getattr(
        details, "cached_tokens", None
    )  # lint-allow: ghost-attribute-read -- litellm usage object
    if read is None:
        read = getattr(
            usage_obj, "cache_read_input_tokens", None
        )  # lint-allow: ghost-attribute-read -- litellm usage object
    write = getattr(
        usage_obj, "cache_creation_input_tokens", None
    )  # lint-allow: ghost-attribute-read -- litellm usage object
    return (
        _count(read, field="cache_read_input_tokens"),
        _count(write, field="cache_write_input_tokens"),
    )


def _count(value: object, *, field: str) -> int:
    """Coerce a reported token count to a non-negative int, zero otherwise.

    Absent is zero silently, because a provider that publishes no cache
    figures is the ordinary case. A value that IS there and is not a count
    (a boolean, a string, a negative number, a fraction of a token) is zero
    too, so the record is kept rather than dropped, but it is said: a
    provider changing the shape of the field would otherwise zero every
    cache figure with nothing to read, and truncating ``1.5`` to ``1`` would
    persist a figure nobody measured.

    Returns:
        The count, or zero for an absent or malformed value.
    """
    if value is None:
        return 0
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value < 0
        or (isinstance(value, float) and not value.is_integer())
    ):
        logger.warning(PROVIDER_COST_INVALID, field=field, value=repr(value))
        return 0
    return int(value)


def _bounded_cache_read(cache_read: int, *, input_tokens: int) -> int:
    """Refuse a cached read larger than the prompt it was read for.

    A count above the prompt total is not a count this record can carry:
    the budget record refuses it and the engine drops the whole record with
    it, silently under-reporting spend. Zero, and said, on the same terms as
    a malformed count, because the true figure is unknowable from here: a
    provider that bills cached reads outside its prompt total has published
    a shape this reader does not know, and inventing a share from it would
    be a number nobody measured.

    Returns:
        *cache_read* when it fits the prompt, else zero.
    """
    if cache_read <= input_tokens:
        return cache_read
    logger.warning(
        PROVIDER_COST_INVALID,
        field="cache_read_input_tokens",
        value=cache_read,
        input_tokens=input_tokens,
        reason="cached read exceeds the prompt total",
    )
    return 0


def token_usage_from_response_usage(
    usage_obj: object,
    *,
    cost_per_1k_input: float,
    cost_per_1k_output: float,
) -> TokenUsage:
    """Build a ``TokenUsage`` from a raw provider usage object.

    Reads ``prompt_tokens`` / ``completion_tokens`` off *usage_obj* (an absent
    or ``None`` attribute counts as 0), then delegates to
    :func:`compute_token_cost`.

    Returns:
        A populated ``TokenUsage`` for the usage object's token counts.
    """
    input_tok = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
    output_tok = int(getattr(usage_obj, "completion_tokens", 0) or 0)
    cache_read, cache_write = _cache_tokens(usage_obj)
    return compute_token_cost(
        input_tok,
        output_tok,
        cost_per_1k_input=cost_per_1k_input,
        cost_per_1k_output=cost_per_1k_output,
        cache_read_input_tokens=_bounded_cache_read(cache_read, input_tokens=input_tok),
        cache_write_input_tokens=cache_write,
    )


def compute_image_cost(n: int, *, cost_per_image: float) -> TokenUsage:
    """Build a ``TokenUsage`` for a per-image-priced generation call.

    Image models bill per generated image, not per token, so the token
    counts are zero and the whole charge lands in ``cost``. When
    ``cost_per_image > 0`` the resulting positive cost keeps the record
    out of the zero-usage skip path so it is attributed to the ambient
    cost scope; an unpriced (``0.0``) model produces a zero-usage record
    that is skipped, exactly like a free-tier token call.

    Args:
        n: Number of images generated (must be >= 1).
        cost_per_image: Flat cost per image in the configured currency
            (finite and >= 0).

    Returns:
        Populated ``TokenUsage`` with zero token counts and the image cost.

    Raises:
        InvalidRequestError: If ``n`` is not positive or ``cost_per_image``
            is negative or non-finite.
    """
    if n < 1:
        msg = "n must be a positive image count"
        logger.warning(PROVIDER_COST_INVALID, field="n", value=n)
        raise InvalidRequestError(msg, context={"n": n})
    if cost_per_image < 0 or not math.isfinite(cost_per_image):
        msg = "cost_per_image must be a finite non-negative number"
        logger.warning(
            PROVIDER_COST_INVALID, field="cost_per_image", value=cost_per_image
        )
        raise InvalidRequestError(msg, context={"cost_per_image": cost_per_image})
    return TokenUsage(
        input_tokens=0,
        output_tokens=0,
        cost=round(n * cost_per_image, BUDGET_ROUNDING_PRECISION),
    )
