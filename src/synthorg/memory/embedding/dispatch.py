# module-kind: code
"""Shared rules for dispatching an embedding call.

The width probe and the serving embedder call the same provider, the same
way, for the same reasons. Keeping the identifier format, the
transient-fault classification, the retry budget, the deadline and the
cost attribution here rather than restating them at each call site is
what stops the two paths drifting: they did, and each ended up missing a
protection the other had.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Final

from litellm.exceptions import (
    APIConnectionError as LiteLLMConnectionError,
)
from litellm.exceptions import (
    InternalServerError as LiteLLMInternalError,
)
from litellm.exceptions import (
    RateLimitError as LiteLLMRateLimit,
)
from litellm.exceptions import (
    ServiceUnavailableError as LiteLLMUnavailable,
)
from litellm.exceptions import (
    Timeout as LiteLLMTimeout,
)

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_EMBEDDING_COST_RECORD_FAILED,
    MEMORY_EMBEDDING_RETRIED,
)
from synthorg.providers.cost_recording import resolve_currency

logger = get_logger(__name__)

# LiteLLM routes on a "provider/model" identifier. Building it here keeps
# the joining rule in one place rather than at every call site.
PROVIDER_MODEL_SEPARATOR: Final[str] = "/"

# Matches the provider-layer defaults: an embedding endpoint fails the
# same way a completion endpoint does (429, 5xx, connection reset), so
# there is no case for a different budget here.
RETRY_MAX_ATTEMPTS: Final[int] = 3
RETRY_BASE_SECONDS: Final[float] = 0.5
RETRY_CAP_SECONDS: Final[float] = 8.0

#: Wall-clock ceiling for one serving batch, retries included. Wider than
#: the probe's because a probe is one short string while a batch is many,
#: but bounded for the same reason: this call sits on the read path of
#: every recall and the write path of every memory, so an endpoint that
#: accepts the connection and never answers would hold its worker for as
#: long as the provider cared to keep the socket open.
DEFAULT_EMBED_TIMEOUT_SECONDS: Final[float] = 60.0

# Cost attribution needs an owner. Embedding is issued by the memory
# subsystem on behalf of the whole company rather than by any one agent
# or task, so it is attributed to the subsystem instead of being charged
# to whichever agent happened to trigger the recall.
SYSTEM_AGENT_ID: Final[NotBlankStr] = NotBlankStr("system:memory")
SYSTEM_TASK_ID: Final[NotBlankStr] = NotBlankStr("system:memory:embedding")

# LiteLLM's own transient exception types. A deterministic fault
# (auth, bad request, model-not-found, content policy) is NOT here: it
# repeats identically, so retrying it only burns the backoff budget on
# the read + write hot path and masks the real cause behind a generic
# retry-exhausted error. Mirrors the completion driver's own mapping.
RETRYABLE_EMBEDDING_ERRORS: Final[tuple[type[Exception], ...]] = (
    LiteLLMRateLimit,
    LiteLLMTimeout,
    LiteLLMUnavailable,
    LiteLLMInternalError,
    LiteLLMConnectionError,
)


def format_model_ref(provider: str, model: str) -> str:
    """Join a binding into the identifier LiteLLM routes on.

    Returns:
        ``"{provider}/{model}"``.
    """
    return f"{provider}{PROVIDER_MODEL_SEPARATOR}{model}"


def is_retryable_embedding_error(exc: Exception) -> bool:
    """Whether an embedding failure is worth another attempt.

    Only genuinely transient provider faults (rate limit, timeout, 5xx,
    connection reset) retry; a deterministic misconfiguration surfaces
    immediately instead of repeating three times.

    Returns:
        ``True`` when the call should be retried.
    """
    return isinstance(exc, RETRYABLE_EMBEDDING_ERRORS)


def embedding_retry_handler(
    event: str = MEMORY_EMBEDDING_RETRIED,
) -> GeneralRetryHandler:
    """Build the retry handler both embedding call sites share.

    Returns:
        A handler retrying only transient provider faults.
    """
    return GeneralRetryHandler(
        retryable=is_retryable_embedding_error,
        max_attempts=RETRY_MAX_ATTEMPTS,
        base=RETRY_BASE_SECONDS,
        cap=RETRY_CAP_SECONDS,
        event=event,
    )


async def with_deadline[T](
    call: Callable[[], Awaitable[T]],
    *,
    timeout_seconds: float,
) -> T:
    """Run *call* under a wall-clock deadline.

    An embedding endpoint that accepts the connection and then never
    answers is indistinguishable from a slow one until a deadline says
    otherwise. Without one, a boot-path call hangs the whole startup and
    a request-path call holds its worker (and, for setup completion, a
    process-wide lock) indefinitely.

    Returns:
        Whatever *call* returned.

    Raises:
        TimeoutError: If the deadline expires first.
    """
    async with asyncio.timeout(timeout_seconds):
        return await call()


async def record_embedding_cost(
    response: object,
    *,
    cost_tracker: CostTrackerProtocol | None,
    provider: str,
    model: str,
) -> None:
    """Attribute one embedding call's spend to the memory subsystem.

    Best-effort: losing a cost record is not worth losing the embedding,
    so a tracker failure is reported and the caller continues.
    """
    if cost_tracker is None:
        return
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    cost = getattr(response, "_hidden_params", {}).get("response_cost") or 0.0
    try:
        await cost_tracker.record(
            CostRecord(
                agent_id=SYSTEM_AGENT_ID,
                task_id=SYSTEM_TASK_ID,
                provider=NotBlankStr(provider),
                model=NotBlankStr(model),
                input_tokens=int(prompt_tokens),
                output_tokens=0,
                cost=float(cost),
                currency=resolve_currency(cost_tracker),
                timestamp=datetime.now(UTC),
                call_category=LLMCallCategory.EMBEDDING,
            )
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- accounting side channel
        reraise_critical(exc)
        logger.warning(
            MEMORY_EMBEDDING_COST_RECORD_FAILED,
            model=format_model_ref(provider, model),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = [
    "DEFAULT_EMBED_TIMEOUT_SECONDS",
    "PROVIDER_MODEL_SEPARATOR",
    "RETRYABLE_EMBEDDING_ERRORS",
    "RETRY_BASE_SECONDS",
    "RETRY_CAP_SECONDS",
    "RETRY_MAX_ATTEMPTS",
    "SYSTEM_AGENT_ID",
    "SYSTEM_TASK_ID",
    "embedding_retry_handler",
    "format_model_ref",
    "is_retryable_embedding_error",
    "record_embedding_cost",
    "with_deadline",
]
