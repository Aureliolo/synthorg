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
) -> TokenUsage:
    """Build a ``TokenUsage`` from raw token counts and per-1k rates.

    Args:
        input_tokens: Number of input tokens (must be >= 0).
        output_tokens: Number of output tokens (must be >= 0).
        cost_per_1k_input: Cost per 1,000 input tokens in the configured
            currency (finite and >= 0).
        cost_per_1k_output: Cost per 1,000 output tokens in the configured
            currency (finite and >= 0).

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
    )


def compute_image_cost(n: int, *, cost_per_image: float) -> TokenUsage:
    """Build a ``TokenUsage`` for a per-image-priced generation call.

    Image models bill per generated image, not per token, so the token
    counts are zero and the whole charge lands in ``cost``. A non-zero
    cost keeps the record out of the zero-usage skip path so it is still
    attributed to the ambient cost scope.

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
