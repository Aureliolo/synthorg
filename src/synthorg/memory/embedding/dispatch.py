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
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import cache
from typing import Final, TypedDict

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
from synthorg.providers.embedding_endpoint import EmbeddingEndpoint
from synthorg.providers.transport_policy import (
    require_confidential_transport,
    require_credentialed_endpoint,
)

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


@cache
def _retryable_embedding_errors() -> tuple[type[Exception], ...]:
    """LiteLLM's own transient exception types.

    A deterministic fault (auth, bad request, model-not-found, content
    policy) is NOT here: it repeats identically, so retrying it only burns
    the backoff budget on the read + write hot path and masks the real
    cause behind a generic retry-exhausted error. Mirrors the completion
    driver's own mapping.

    Resolved on first use rather than at import. This module sits on the
    import path of ``synthorg.api.app`` (via the memory embedder, reached
    from the meeting conflict detector), and importing litellm costs ~2.5s,
    which every app boot, every cold-import test and every xdist worker's
    collection was paying to build a tuple used by one ``isinstance``.

    Both routes to that check -- ``probe.py`` and
    ``text_embedder.ProviderTextEmbedder.embed_many`` -- defer their own
    ``from litellm import aembedding`` and raise only after calling it, so
    litellm is resident by the time an error needs classifying and this
    import is free. That is a property of every caller, not of a single
    chokepoint: a new call site reaching the classifier without importing
    litellm first would pay the ~2.5s here, inside a failing request.

    Returns:
        The exception types worth another attempt.
    """
    from litellm.exceptions import (  # noqa: PLC0415 -- deferred: ~2.5s cold
        APIConnectionError,
        InternalServerError,
        RateLimitError,
        ServiceUnavailableError,
        Timeout,
    )

    return (
        RateLimitError,
        Timeout,
        ServiceUnavailableError,
        InternalServerError,
        APIConnectionError,
    )


def format_model_ref(provider: str, model: str) -> str:
    """Join a binding into the identifier LiteLLM routes on.

    Returns:
        ``"{provider}/{model}"``.
    """
    return f"{provider}{PROVIDER_MODEL_SEPARATOR}{model}"


class _EmbeddingRequiredKwargs(TypedDict):
    """Keyword arguments every ``aembedding`` call sets."""

    model: str
    input: list[str]


class EmbeddingKwargs(_EmbeddingRequiredKwargs, total=False):
    """Typed view of the arguments handed to ``litellm.aembedding``.

    The transport keys are optional because a hosted provider that declares
    no base URL and needs no credential legitimately sets none of them;
    litellm types the parameters individually, so splatting an opaque dict
    would not type-check at the call site.
    """

    api_base: str
    api_key: str
    extra_headers: dict[str, str]


def embedding_kwargs(
    *,
    model_ref: str,
    inputs: list[str],
    endpoint: EmbeddingEndpoint | None,
) -> EmbeddingKwargs:
    """Assemble one ``aembedding`` call, addressed at the operator's endpoint.

    A model reference alone leaves litellm to pick a host from its own
    defaults, which for a self-hosted provider is the wrong machine and no
    amount of provider configuration can correct. Both embedding call sites
    build their request here so neither can regress to that.

    Returns:
        The keyword arguments for ``litellm.aembedding``.

    Raises:
        ProviderValidationError: If the endpoint would be addressed in
            cleartext beyond this machine's own network, or carries a
            credential with no endpoint to send it to. Re-checked here
            rather than trusted from resolution because this is the
            boundary every embedding call passes through, and an endpoint
            can be constructed without going through one.
    """
    kwargs: EmbeddingKwargs = {"model": model_ref, "input": inputs}
    if endpoint is None:
        return kwargs
    # ``inputs`` is the text being embedded, so the destination is checked
    # whether or not a credential rides along with it.
    require_confidential_transport(endpoint.api_base, field="Embedding endpoint")
    if endpoint.api_key is not None or endpoint.extra_headers:
        require_credentialed_endpoint(endpoint.api_base, field="Embedding endpoint")
    if endpoint.api_base is not None:
        kwargs["api_base"] = endpoint.api_base
    if endpoint.api_key is not None:
        kwargs["api_key"] = endpoint.api_key
    if endpoint.extra_headers:
        kwargs["extra_headers"] = dict(endpoint.extra_headers)
    return kwargs


def is_retryable_embedding_error(exc: Exception) -> bool:
    """Whether an embedding failure is worth another attempt.

    Only genuinely transient provider faults (rate limit, timeout, 5xx,
    connection reset) retry; a deterministic misconfiguration surfaces
    immediately instead of repeating three times.

    Returns:
        ``True`` when the call should be retried.
    """
    return isinstance(exc, _retryable_embedding_errors())


def _embedding_retry_after(exc: Exception) -> float | None:
    """Return the provider's own ``Retry-After`` hint, when it gave one.

    Read by attribute rather than by exception class: the driver surfaces
    whatever error type the provider raised, and no single class covers them.
    A hint that cannot be read leaves the computed backoff in place, which is
    the safe default: retrying sooner than a rate limit allows just spends
    another request to be refused again.

    Returns:
        The advertised delay in seconds, or ``None`` when there is none.
    """
    raw: object = getattr(exc, "retry_after", None)
    if raw is None:
        headers = getattr(getattr(exc, "response", None), "headers", None)
        raw = headers.get("retry-after") if headers is not None else None
    if raw is None:
        return None
    try:
        seconds = float(raw)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None
    # A hint wins over the computed backoff and is deliberately not
    # re-capped, so "inf" would park the retry forever on the say-so of the
    # endpoint that just refused the call.
    if not math.isfinite(seconds):
        return None
    return seconds if seconds > 0 else None


def embedding_retry_handler(
    event: str = MEMORY_EMBEDDING_RETRIED,
) -> GeneralRetryHandler:
    """Build the retry handler both embedding call sites share.

    Returns:
        A handler retrying only transient provider faults, honouring a
        provider-advertised retry delay over its own computed backoff.
    """
    return GeneralRetryHandler(
        retryable=is_retryable_embedding_error,
        max_attempts=RETRY_MAX_ATTEMPTS,
        base=RETRY_BASE_SECONDS,
        cap=RETRY_CAP_SECONDS,
        event=event,
        delay_override=_embedding_retry_after,
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
                # Embedding is issued by the memory subsystem for the whole
                # company, so it is charged to no agent and no task rather
                # than to whichever agent happened to trigger the recall.
                # It also has no prompt class, because there is no system
                # prompt: ``call_category`` is what carries its purpose.
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
    "RETRY_BASE_SECONDS",
    "RETRY_CAP_SECONDS",
    "RETRY_MAX_ATTEMPTS",
    "EmbeddingKwargs",
    "embedding_kwargs",
    "embedding_retry_handler",
    "format_model_ref",
    "is_retryable_embedding_error",
    "record_embedding_cost",
    "with_deadline",
]
