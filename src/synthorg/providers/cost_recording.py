"""Per-call cost recording chokepoint for ``BaseCompletionProvider``.

Mirrors :mod:`synthorg.observability.correlation`'s
:func:`correlation_scope` pattern: a context-variable-backed scope
binds per-call recording context (``cost_tracker``, ``agent_id``,
``task_id``, optional ``project_id``, ``call_category``, ``currency``)
that the chokepoint inside :class:`BaseCompletionProvider.complete`
reads after a successful response.

Sites that do not open a scope (probes, model-discovery, tests) see
no behavior change -- the chokepoint reads ``None`` and is a no-op.

The scope is async-safe: Python :mod:`contextvars` propagate per
``asyncio.Task``.
"""

import asyncio
import math
from collections.abc import (
    AsyncIterator,
    Mapping,
)
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode

# ``CostTracker`` and ``CompletionResponse`` appear in public
# annotations on ``cost_recording_scope`` / ``resolve_currency`` /
# ``emit_cost_record_from_context``, so they must resolve at runtime
# when downstream tooling evaluates type hints.
from synthorg.budget.tracker import CostTracker
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_COST_FAILED,
    PROVIDER_COST_RECORDED,
    PROVIDER_COST_SKIPPED,
)
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
)

logger = get_logger(__name__)


class CostRecordingContext(BaseModel):
    """Per-call recording context bound to the current ``asyncio.Task``.

    Construction is via :func:`cost_recording_scope` rather than direct
    instantiation.  ``cost_tracker`` is a :class:`CostTracker` (a
    non-Pydantic class), permitted by ``arbitrary_types_allowed``; the
    field validator raises ``ValueError`` on a bad instance so Pydantic
    surfaces it as a ``ValidationError``.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    cost_tracker: CostTracker = Field(description="CostTracker reference")
    agent_id: NotBlankStr = Field(description="Agent attribution")
    task_id: NotBlankStr = Field(description="Task attribution")
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Optional project attribution",
    )
    call_category: LLMCallCategory = Field(description="LLM call category")
    currency: CurrencyCode = Field(
        description="ISO 4217 currency for emitted CostRecord",
    )

    @field_validator("cost_tracker", mode="before")
    @classmethod
    def _validate_cost_tracker(cls, value: object) -> object:
        """Validate that ``cost_tracker`` is a ``CostTracker`` instance.

        Runs in ``before`` mode so the explicit rejection fires ahead of
        Pydantic's core ``is_instance_of`` check. Raises ``ValueError`` so
        Pydantic wraps it into a ``ValidationError`` with a field path; a
        bare ``TypeError`` would escape the model-construction call
        uncaught.

        Returns:
            The validated ``CostTracker`` value.

        Raises:
            ValueError: If *value* is not a ``CostTracker`` instance.
        """
        from synthorg.budget.tracker import CostTracker  # noqa: PLC0415

        if not isinstance(value, CostTracker):
            msg = (
                f"cost_tracker must be a CostTracker instance, got "
                f"{type(value).__name__}"
            )
            raise ValueError(msg)  # noqa: TRY004 -- Pydantic needs ValueError
        return value


# Bound on ``cost_tracker.record(...)`` so a slow or hung tracker
# (DB stall, queue backpressure, custom Tracker subclass with bad
# I/O) can never accumulate forever-pending background tasks.  5 s
# is well under any reasonable LLM call duration; the chokepoint
# itself does not await this -- the bound only protects against
# leaked tasks if the tracker hangs indefinitely.
_COST_RECORD_TIMEOUT_SECONDS: Final[float] = 5.0


# Strong references to in-flight background record tasks live on the
# active :class:`CostTracker` (one per :class:`AppState`, fresh per
# test) so xdist workers cannot leak tasks bound to a closed event
# loop into the next test's loop. Tests await
# ``cost_tracker.drain_pending_records()`` to observe tracker state
# immediately after a ``provider.complete()`` call.
_cost_context: ContextVar[CostRecordingContext | None] = ContextVar(
    "synthorg_cost_recording_context",
    default=None,
)


def current_cost_context() -> CostRecordingContext | None:
    """Return the cost-recording context active in the current task.

    Returns:
        The active :class:`CostRecordingContext`, or ``None`` when no
        scope is open in the current asyncio task.
    """
    return _cost_context.get()


@asynccontextmanager
async def cost_recording_scope(  # noqa: PLR0913
    *,
    cost_tracker: CostTracker | None,
    agent_id: NotBlankStr,
    task_id: NotBlankStr,
    project_id: NotBlankStr | None = None,
    call_category: LLMCallCategory,
    currency: CurrencyCode | None = None,
) -> AsyncIterator[None]:
    """Open a cost-recording scope for the current asyncio task.

    Every ``BaseCompletionProvider.complete()`` call inside the scope
    emits a :class:`CostRecord` to ``cost_tracker``.  Nested scopes
    shadow the outer one and the prior scope is restored on exit.

    When ``cost_tracker`` is ``None`` the scope still **shadows the
    outer context with ``None``** -- so a nested ``cost_tracker=None``
    block under an outer wired scope correctly suppresses recording
    for the nested call rather than silently inheriting the outer
    tracker.  The original context is restored on exit.

    Args:
        cost_tracker: Sink the chokepoint records to.  ``None`` makes
            the scope a no-op (suppresses recording for the duration
            of the block, including nested calls under a wired outer
            scope).
        agent_id: Agent attribution for the emitted record.
        task_id: Task attribution for the emitted record.
        project_id: Optional project attribution.
        call_category: Category to stamp on the emitted record.
        currency: ISO 4217 currency for the emitted record.  Required
            when ``cost_tracker`` is provided; when ``None`` the
            tracker's ``budget_config.currency`` (or
            :data:`DEFAULT_CURRENCY`) is used.
    """
    if cost_tracker is None:
        # Shadow the outer context with ``None`` so nested calls
        # don't silently inherit a wired outer tracker.  Reset on
        # exit to restore whatever was active before.
        token = _cost_context.set(None)
        try:
            yield
        finally:
            _cost_context.reset(token)
        return
    resolved_currency = (
        currency if currency is not None else resolve_currency(cost_tracker)
    )
    ctx = CostRecordingContext(
        cost_tracker=cost_tracker,
        agent_id=agent_id,
        task_id=task_id,
        project_id=project_id,
        call_category=call_category,
        currency=resolved_currency,
    )
    token = _cost_context.set(ctx)
    try:
        yield
    finally:
        _cost_context.reset(token)


def resolve_currency(cost_tracker: CostTracker) -> CurrencyCode:
    """Return the currency to stamp on records from ``cost_tracker``.

    Reads ``budget_config.currency`` when a budget config is attached;
    otherwise falls back to :data:`DEFAULT_CURRENCY`.  Mirrors the
    existing convention in
    :mod:`synthorg.engine.cost_recording` and
    :mod:`synthorg.hr.performance.llm_judge_quality_strategy`.

    Args:
        cost_tracker: Tracker whose budget config drives the currency.

    Returns:
        ISO 4217 currency code.
    """
    config = getattr(cost_tracker, "budget_config", None)
    if config is not None:
        return CurrencyCode(config.currency)
    return CurrencyCode(DEFAULT_CURRENCY)


def _is_zero_usage(usage: TokenUsage) -> bool:
    """True when a response has zero cost AND zero tokens.

    Returns:
        ``True`` when the cost and both token counts of *usage* are all
        exactly zero; ``False`` otherwise.
    """
    return usage.cost == 0.0 and usage.input_tokens == 0 and usage.output_tokens == 0


def _extract_provider_metadata(
    metadata: Mapping[str, object] | None,
) -> tuple[float | None, bool | None, int | None, str | None]:
    """Extract typed ``_synthorg_*`` metadata fields from a response.

    Returns a tuple of (latency_ms, cache_hit, retry_count, retry_reason)
    with each value coerced to its expected type or ``None`` when absent
    or mistyped.  Mirrors the extraction in
    :func:`synthorg.engine.loop_helpers.make_turn_record`.

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


# FinishReason values that represent a non-successful terminal outcome
# from the provider.  ``ERROR`` is the only enum member that signals an
# unsuccessful completion the chokepoint can observe; ``STOP``,
# ``MAX_TOKENS``, ``TOOL_USE``, and ``CONTENT_FILTER`` are all legitimate
# terminal reasons whose cost should still record as success.
_NON_SUCCESS_FINISH_REASONS: Final[frozenset[str]] = frozenset({"error"})


def _is_successful_finish(finish_reason: object) -> bool:
    """Derive ``CostRecord.success`` from the provider's finish reason.

    The chokepoint only fires after ``provider.complete()`` returns
    without raising, so most calls land here as successes.  A response
    that returns normally but carries ``FinishReason.ERROR`` indicates
    a model-side failure (refusal, content-filter trip, internal error
    surfaced as an error finish) -- record the cost (we still paid for
    the tokens) but mark the record as ``success=False`` so analytics
    can break out failed-but-billed calls.

    Returns:
        ``True`` when the finish reason is absent or any value other than
        ``ERROR``; ``False`` for an ``ERROR`` finish.
    """
    if finish_reason is None:
        return True
    value = getattr(finish_reason, "value", finish_reason)
    return str(value) not in _NON_SUCCESS_FINISH_REASONS


def _build_cost_record(
    ctx: CostRecordingContext,
    response: CompletionResponse,
    *,
    model: str,
    provider: str,
) -> CostRecord:
    """Construct a CostRecord from the active context + response.

    Returns:
        A ``CostRecord`` populated from the active context, the response
        usage/cost, and the extracted provider metadata.
    """
    latency_ms, cache_hit, retry_count, retry_reason = _extract_provider_metadata(
        response.provider_metadata,
    )
    usage = response.usage
    return CostRecord(
        agent_id=ctx.agent_id,
        task_id=ctx.task_id,
        project_id=ctx.project_id,
        provider=NotBlankStr(provider),
        model=NotBlankStr(model),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost=usage.cost,
        currency=ctx.currency,
        timestamp=datetime.now(UTC),
        call_category=ctx.call_category,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        retry_count=retry_count,
        retry_reason=retry_reason,
        finish_reason=response.finish_reason,
        success=_is_successful_finish(response.finish_reason),
    )


async def emit_cost_record_from_context(
    ctx: CostRecordingContext,
    response: CompletionResponse,
    *,
    model: str,
    provider: str,
) -> None:
    """Build and submit a :class:`CostRecord` from a completion response.

    Skips when the response carries zero cost AND zero tokens (matches
    the engine's existing rule for free-tier no-ops).  Recording
    failures are logged at WARNING and swallowed; the provider call's
    user-visible result must not depend on recording success.

    ``MemoryError`` and ``RecursionError`` propagate.

    Args:
        ctx: Active recording context.
        response: Successful completion response from the provider.
        model: Model identifier the provider returned for this call.
        provider: Provider label resolved by the base class.
    """
    if _is_zero_usage(response.usage):
        logger.debug(
            PROVIDER_COST_SKIPPED,
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
            provider=provider,
            model=model,
            reason="zero_cost_and_tokens",
        )
        return

    try:
        record = _build_cost_record(ctx, response, model=model, provider=provider)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_COST_FAILED,
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
            provider=provider,
            model=model,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            reason="cost_record_construction_failed",
        )
        return

    # Submit the record off the response path so a slow tracker can
    # never add user-visible latency to ``provider.complete()``.  The
    # background task is bounded by the same timeout so a hung
    # tracker doesn't accumulate forever-pending tasks; failures
    # (timeout, exception) are logged in the task itself with the
    # same structured event the inline path used to emit.  The
    # ``PROVIDER_COST_RECORDED`` INFO log fires from
    # ``_record_cost_in_background`` only after the tracker actually
    # accepts the record, so a hung/failing tracker no longer produces
    # a misleading "recorded" log followed by a "failed" warning.
    task = asyncio.create_task(
        _record_cost_in_background(ctx, record, provider=provider, model=model),
        name=f"cost_record:{ctx.agent_id}:{ctx.task_id}",
    )
    # Track task on the active tracker so the event loop's GC can't
    # drop the reference and cancel the recording mid-flight. The
    # tracker's ``add_done_callback`` plumbing self-evicts the task
    # once it completes; ownership on the per-instance tracker means
    # xdist test isolation is automatic.
    ctx.cost_tracker.track_pending_record(task)


async def _record_cost_in_background(
    ctx: CostRecordingContext,
    record: CostRecord,
    *,
    provider: str,
    model: str,
) -> None:
    """Submit a CostRecord with bounded latency, swallow + log failures.

    Run as a background task by ``emit_cost_record_from_context`` so
    the user-visible provider response returns immediately.  Bounded
    by ``_COST_RECORD_TIMEOUT_SECONDS`` so a hung tracker doesn't
    leak tasks.  ``MemoryError`` / ``RecursionError`` propagate to
    the asyncio event loop's default exception handler (loud crash
    is preferable to silent corruption); everything else is logged
    and swallowed.
    """
    try:
        await asyncio.wait_for(
            ctx.cost_tracker.record(record),
            timeout=_COST_RECORD_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        logger.warning(
            PROVIDER_COST_FAILED,
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
            provider=provider,
            model=model,
            error_type=type(exc).__name__,
            timeout_seconds=_COST_RECORD_TIMEOUT_SECONDS,
            reason="cost_tracker_record_timeout",
        )
        return
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            PROVIDER_COST_FAILED,
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
            provider=provider,
            model=model,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            reason="cost_tracker_record_failed",
        )
        return

    # Only log success after the tracker has actually accepted the
    # record -- emitting at ``create_task`` time would produce a
    # misleading INFO log when the background submission later fails.
    logger.info(
        PROVIDER_COST_RECORDED,
        agent_id=ctx.agent_id,
        task_id=ctx.task_id,
        provider=provider,
        model=model,
        cost=record.cost,
        currency=ctx.currency,
        call_category=ctx.call_category.value,
        success=record.success,
    )


__all__ = [
    "CostRecordingContext",
    "cost_recording_scope",
    "current_cost_context",
    "emit_cost_record_from_context",
    "resolve_currency",
]
