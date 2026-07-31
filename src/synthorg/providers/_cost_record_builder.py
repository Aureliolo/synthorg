"""Turning a provider response into a ``CostRecord``.

Separate from the recording scope: deciding what a response cost and
whether it succeeded is provider-shape parsing, while the scope owns who
the call belongs to and how the record is submitted.
"""

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, TypedDict

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import CurrencyCode
from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.providers.models import CompletionResponse, TokenUsage

if TYPE_CHECKING:
    from synthorg.providers.cost_recording import CostRecordingContext

# FinishReason values that represent a non-successful terminal outcome
# from the provider.  ``ERROR`` is the only enum member that signals an
# unsuccessful completion the chokepoint can observe; ``STOP``,
# ``MAX_TOKENS``, ``TOOL_USE``, and ``CONTENT_FILTER`` are all legitimate
# terminal reasons whose cost should still record as success.
_NON_SUCCESS_FINISH_REASONS: Final[frozenset[str]] = frozenset({"error"})


def is_zero_usage(usage: TokenUsage) -> bool:
    """True when a response has zero cost AND zero tokens.

    Args:
        usage: The response's token usage.

    Returns:
        ``True`` when the cost and both token counts of *usage* are all
        exactly zero; ``False`` otherwise.
    """
    return usage.cost == 0.0 and usage.input_tokens == 0 and usage.output_tokens == 0


def extract_provider_metadata(
    metadata: Mapping[str, object] | None,
) -> tuple[float | None, bool | None, int | None, str | None]:
    """Extract typed ``_synthorg_*`` metadata fields from a response.

    Returns a tuple of (latency_ms, cache_hit, retry_count, retry_reason)
    with each value coerced to its expected type or ``None`` when absent
    or mistyped.  Mirrors the extraction in
    :func:`synthorg.engine.loop_helpers.make_turn_record`.

    Args:
        metadata: The response's provider metadata, when it carries any.

    Returns:
        A ``(latency_ms, cache_hit, retry_count, retry_reason)`` tuple,
        each field coerced to its typed value or ``None`` when absent or
        of unexpected type.
    """
    md = metadata or {}
    latency_raw = md.get("_synthorg_latency_ms")
    cache_raw = md.get("_synthorg_cache_hit")
    retry_count_raw = md.get("_synthorg_retry_count")
    retry_reason_raw = md.get("_synthorg_retry_reason")

    # Reject NaN/Inf and ``bool`` at the boundary so a misbehaving
    # driver can't poison the recorded numeric fields -- ``bool`` is
    # an ``int`` subclass so ``isinstance(True, int)`` is True; an
    # explicit ``not isinstance(..., bool)`` guard keeps booleans
    # from being silently coerced to ``1.0`` / ``1``.  ``CostRecord``
    # carries ``allow_inf_nan=False`` and would raise on validation,
    # which the chokepoint would swallow as
    # "cost_record_construction_failed" -- filtering here turns a
    # corrupt field into a missing one instead of a dropped record.
    if (
        isinstance(latency_raw, (int, float))
        and not isinstance(latency_raw, bool)
        and math.isfinite(latency_raw)
    ):
        latency_ms: float | None = float(latency_raw)
    else:
        latency_ms = None
    cache_hit = cache_raw if isinstance(cache_raw, bool) else None
    if isinstance(retry_count_raw, int) and not isinstance(retry_count_raw, bool):
        retry_count: int | None = retry_count_raw
    else:
        retry_count = None
    retry_reason = retry_reason_raw if isinstance(retry_reason_raw, str) else None
    return latency_ms, cache_hit, retry_count, retry_reason


def is_successful_finish(finish_reason: object) -> bool:
    """Derive ``CostRecord.success`` from the provider's finish reason.

    The chokepoint only fires after ``provider.complete()`` returns
    without raising, so most calls land here as successes.  A response
    that returns normally but carries ``FinishReason.ERROR`` indicates
    a model-side failure (refusal, content-filter trip, internal error
    surfaced as an error finish) -- record the cost (we still paid for
    the tokens) but mark the record as ``success=False`` so analytics
    can break out failed-but-billed calls.

    Args:
        finish_reason: The response's finish reason, when it carries one.

    Returns:
        ``True`` when the finish reason is absent or any value other than
        ``ERROR``; ``False`` for an ``ERROR`` finish.
    """
    if finish_reason is None:
        return True
    value = getattr(finish_reason, "value", finish_reason)
    return str(value) not in _NON_SUCCESS_FINISH_REASONS


class _ScopeFields(TypedDict):
    """The fields every ``CostRecord`` reads off the scope and the usage.

    Extracted so the two builders cannot drift: they differ only in where
    the provider metadata comes from, and a field added to one call site
    but not the other is invisible until the analytics that needed it come
    back empty.
    """

    agent_id: NotBlankStr | None
    task_id: NotBlankStr | None
    project_id: NotBlankStr | None
    prompt_class_id: NotBlankStr | None
    provider: NotBlankStr
    model: NotBlankStr
    input_tokens: int
    output_tokens: int
    cost: float
    currency: CurrencyCode
    timestamp: datetime


def _scope_fields(
    ctx: CostRecordingContext,
    usage: TokenUsage,
    *,
    model: str,
    provider: str,
) -> _ScopeFields:
    """Bind the owner, the binding and the usage into one field mapping.

    Returns:
        The shared ``CostRecord`` keyword arguments.
    """
    return _ScopeFields(
        agent_id=ctx.agent_id,
        task_id=ctx.task_id,
        project_id=ctx.project_id,
        prompt_class_id=ctx.prompt_class_id,
        provider=NotBlankStr(provider),
        model=NotBlankStr(model),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost=usage.cost,
        currency=ctx.currency,
        timestamp=datetime.now(UTC),
    )


def build_cost_record(
    ctx: CostRecordingContext,
    response: CompletionResponse,
    *,
    model: str,
    provider: str,
) -> CostRecord:
    """Construct a CostRecord from the active context + response.

    Args:
        ctx: The active recording scope, carrying the owner and currency.
        response: The provider response being charged for.
        model: Model identifier the call resolved to.
        provider: Provider name the call resolved to.

    Returns:
        A ``CostRecord`` populated from the active context, the response
        usage/cost, and the extracted provider metadata.
    """
    latency_ms, cache_hit, retry_count, retry_reason = extract_provider_metadata(
        response.provider_metadata,
    )
    return CostRecord(
        **_scope_fields(ctx, response.usage, model=model, provider=provider),
        call_category=ctx.call_category,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        retry_count=retry_count,
        retry_reason=retry_reason,
        finish_reason=response.finish_reason,
        success=is_successful_finish(response.finish_reason),
    )


def build_cost_record_from_usage(
    ctx: CostRecordingContext,
    usage: TokenUsage,
    *,
    model: str,
    provider: str,
    finish_reason: FinishReason,
    call_category: LLMCallCategory | None = None,
) -> CostRecord:
    """Construct a CostRecord from a bare usage record.

    Used by the streaming path (terminal ``USAGE`` chunk) and the
    image-generation path (per-image cost, zero tokens). ``call_category``
    overrides ``ctx.call_category`` so a non-token modality is categorised
    on the record regardless of the ambient scope.

    Args:
        ctx: The active recording scope, carrying the owner and currency.
        usage: Token usage and cost for the call.
        model: Model identifier the call resolved to.
        provider: Provider name the call resolved to.
        finish_reason: Terminal reason reported for the call.
        call_category: Overrides the scope's category for a non-token
            modality.

    Returns:
        A ``CostRecord`` populated from the active context + usage.
    """
    return CostRecord(
        **_scope_fields(ctx, usage, model=model, provider=provider),
        call_category=call_category if call_category is not None else ctx.call_category,
        latency_ms=None,
        cache_hit=None,
        retry_count=None,
        retry_reason=None,
        finish_reason=finish_reason,
        success=is_successful_finish(finish_reason),
    )
