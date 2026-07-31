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
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode

# ``CostTrackerProtocol`` and ``CompletionResponse`` appear in public
# annotations on ``cost_recording_scope`` / ``resolve_currency`` /
# ``emit_cost_record_from_context``, so they must resolve at runtime
# when downstream tooling evaluates type hints. The chokepoint depends on
# the record/aggregate Protocol surface, never the concrete ``CostTracker``.
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.completion_enums import FinishReason
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_COST_FAILED,
    PROVIDER_COST_RECORDED,
    PROVIDER_COST_RECOVERED,
    PROVIDER_COST_SKIPPED,
    PROVIDER_PROMPT_PURPOSE_INVOKED,
)
from synthorg.providers._cost_record_builder import (
    build_cost_record,
    build_cost_record_from_usage,
    is_zero_usage,
)
from synthorg.providers.models import (
    CompletionResponse,
    TokenUsage,
)

logger = get_logger(__name__)


class CostRecordingContext(BaseModel):
    """Per-call recording context bound to the current ``asyncio.Task``.

    Construction is via :func:`cost_recording_scope` rather than direct
    instantiation.  ``cost_tracker`` is any object satisfying
    :class:`CostTrackerProtocol` (a non-Pydantic type), permitted by
    ``arbitrary_types_allowed``; the field validator enforces the structural
    check at construction and raises ``ValueError`` on a bad instance so
    Pydantic surfaces it as a ``ValidationError``.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        arbitrary_types_allowed=True,
        extra="forbid",
    )

    cost_tracker: CostTrackerProtocol = Field(description="CostTracker reference")
    agent_id: NotBlankStr | None = Field(
        default=None,
        description="Owning agent; None for work no agent owns",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Owning task; None for work that is not a task",
    )
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Optional project attribution",
    )
    prompt_class_id: PromptPurposeId | None = Field(
        default=None,
        description="Optional prompt-purpose attribution",
    )
    call_category: LLMCallCategory = Field(description="LLM call category")
    currency: CurrencyCode = Field(
        description="ISO 4217 currency for emitted CostRecord",
    )

    @field_validator("cost_tracker", mode="before")
    @classmethod
    def _validate_cost_tracker(cls, value: object) -> object:
        """Validate that ``cost_tracker`` satisfies ``CostTrackerProtocol``.

        Runs in ``before`` mode so the explicit rejection fires ahead of
        Pydantic's core ``is_instance_of`` check. Raises ``ValueError`` so
        Pydantic wraps it into a ``ValidationError`` with a field path; a
        bare ``TypeError`` would escape the model-construction call
        uncaught. The check is structural (the ``@runtime_checkable``
        Protocol's record/aggregate surface), not concrete-class identity.

        Returns:
            The validated cost-tracker value.

        Raises:
            ValueError: If *value* does not satisfy ``CostTrackerProtocol``.
        """
        if not isinstance(value, CostTrackerProtocol):
            msg = (
                f"cost_tracker must be a CostTracker (CostTrackerProtocol) "
                f"instance, got {type(value).__name__}"
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

# Dropping one cost record is a blip worth a WARNING; dropping them in a run
# is a standing fault that under-reports the budget for as long as it lasts,
# and some causes never resolve on their own (a tracker whose configured
# currency disagrees with the record's rejects every write, deterministically).
# Without a streak count those are indistinguishable, so a permanent fault
# reads as a series of unrelated blips and nothing ever raises its voice.
COST_FAILURE_ESCALATION_STREAK: Final[int] = 3

# Module-level rather than per-context: the question an operator needs
# answered is whether cost recording as a whole is failing, and a per-call
# context is gone before the second failure happens.
_consecutive_cost_failures: int = 0


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


def consecutive_cost_failures() -> int:
    """How many cost records have failed to land back to back.

    Returns:
        The current failure streak; ``0`` once a record lands.
    """
    return _consecutive_cost_failures


def _note_cost_failure(**fields: object) -> None:
    """Record one failed cost write, escalating once it becomes a pattern.

    A single dropped record is best-effort noise. A run of them means the
    budget is under-reporting continuously, which is the failure this whole
    path exists to prevent, so past the streak threshold it stops being a
    WARNING nobody reads and becomes an ERROR.
    """
    global _consecutive_cost_failures  # noqa: PLW0603 -- module-level streak
    _consecutive_cost_failures += 1
    if _consecutive_cost_failures >= COST_FAILURE_ESCALATION_STREAK:
        logger.error(
            PROVIDER_COST_FAILED,
            consecutive_failures=_consecutive_cost_failures,
            **fields,
        )
        return
    logger.warning(
        PROVIDER_COST_FAILED,
        consecutive_failures=_consecutive_cost_failures,
        **fields,
    )


def _note_cost_success() -> None:
    """Clear the failure streak, announcing recovery only if there was one."""
    global _consecutive_cost_failures  # noqa: PLW0603 -- module-level streak
    if _consecutive_cost_failures >= COST_FAILURE_ESCALATION_STREAK:
        logger.info(
            PROVIDER_COST_RECOVERED,
            dropped_records=_consecutive_cost_failures,
        )
    _consecutive_cost_failures = 0


def current_cost_context() -> CostRecordingContext | None:
    """Return the cost-recording context active in the current task.

    Returns:
        The active :class:`CostRecordingContext`, or ``None`` when no
        scope is open in the current asyncio task.
    """
    return _cost_context.get()


@contextmanager
def _bound_cost_context(value: CostRecordingContext | None) -> Iterator[None]:
    """Bind ``_cost_context`` to *value*, restoring the prior value on exit.

    Restoration is a plain ``set(previous)`` rather than ``Token.reset``:
    a streaming scope wraps an SSE async generator whose enter and exit can
    run in different asyncio contexts (a drive step vs its teardown), where
    ``Token.reset`` raises ``ValueError``; ``set`` is valid in any context.
    """
    previous = _cost_context.get()
    _cost_context.set(value)
    try:
        yield
    finally:
        _cost_context.set(previous)


@asynccontextmanager
async def cost_recording_scope(
    *,
    cost_tracker: CostTrackerProtocol | None,
    agent_id: NotBlankStr | None = None,
    task_id: NotBlankStr | None = None,
    project_id: NotBlankStr | None = None,
    purpose: PromptPurposeId | None = None,
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
        agent_id: Owning agent, or ``None`` for work no agent owns.
        task_id: Owning task, or ``None`` for work that is not a task.
            ``task_id`` is a foreign key into ``tasks``, so subsystem work
            leaves both unset rather than inventing an id the table cannot
            resolve; ``purpose`` is what identifies such a call.
        project_id: Optional project attribution.
        purpose: Optional prompt-purpose attribution stamped on the
            emitted record's ``prompt_class_id`` so spend can be sliced by
            prompt purpose. ``None`` when the call carries no system prompt
            purpose.
        call_category: Category to stamp on the emitted record.
        currency: ISO 4217 currency for the emitted record.  Required
            when ``cost_tracker`` is provided; when ``None`` the
            tracker's ``budget_config.currency`` (or
            :data:`DEFAULT_CURRENCY`) is used.
    """
    if purpose is not None:
        # Coerce up front so the trackerless path shares the tracked path's
        # contract: a mistyped raw string is rejected here instead of leaking
        # into eval/analytics logs as a bogus prompt_class_id.
        purpose = PromptPurposeId(str(purpose))
        # Emit before the tracker check so a registered prompt purpose is
        # observable even with no cost tracker wired (the evals harness runs
        # trackerless; it counts these to attribute spend signal by purpose).
        logger.debug(PROVIDER_PROMPT_PURPOSE_INVOKED, prompt_class_id=str(purpose))
    if cost_tracker is None:
        # Shadow the outer context with ``None`` so nested calls don't
        # silently inherit a wired outer tracker.
        with _bound_cost_context(None):
            yield
        return
    resolved_currency = (
        currency if currency is not None else resolve_currency(cost_tracker)
    )
    ctx = CostRecordingContext(
        cost_tracker=cost_tracker,
        agent_id=agent_id,
        task_id=task_id,
        project_id=project_id,
        # The public ``purpose`` kwarg maps onto the context's
        # ``prompt_class_id``: call sites read as ``purpose=...`` while the
        # stored field name matches ``CostRecord.prompt_class_id`` / the DB column.
        prompt_class_id=purpose,
        call_category=call_category,
        currency=resolved_currency,
    )
    with _bound_cost_context(ctx):
        yield


def resolve_currency(cost_tracker: CostTrackerProtocol) -> CurrencyCode:
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


async def _skip_build_and_submit(
    ctx: CostRecordingContext,
    usage: TokenUsage,
    *,
    model: str,
    provider: str,
    build: Callable[[], CostRecord],
) -> None:
    """Skip free no-ops, build the record, and submit it off-path.

    Shared by the completion and streaming emitters. A zero-cost AND
    zero-token call is skipped (free-tier no-op). A build failure is
    logged at WARNING and swallowed -- the provider call's user-visible
    result must not depend on recording success. Otherwise the record is
    submitted on a tracked background task so a slow tracker cannot add
    user-visible latency; the task is bounded and owned by the
    per-instance tracker (GC-safe, xdist-isolated). ``MemoryError`` /
    ``RecursionError`` propagate.
    """
    if is_zero_usage(usage):
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
        record = build()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        _note_cost_failure(
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
    # (timeout, exception) are logged in the task itself with
    # ``PROVIDER_COST_FAILED``.  The ``PROVIDER_COST_RECORDED`` INFO log
    # fires from ``_record_cost_in_background`` only after the tracker
    # actually accepts the record, so a hung/failing tracker no longer
    # produces a misleading "recorded" log followed by a "failed" warning.
    task = asyncio.create_task(
        _record_cost_in_background(ctx, record, provider=provider, model=model),
        name=f"cost_record:{ctx.agent_id}:{ctx.task_id}",
    )
    ctx.cost_tracker.track_pending_record(task)


async def emit_cost_record_from_context(
    ctx: CostRecordingContext,
    response: CompletionResponse,
    *,
    model: str,
    provider: str,
) -> None:
    """Build and submit a :class:`CostRecord` from a completion response.

    Skips free-tier no-ops, swallows recording failures, and submits the
    record off the response path; see :func:`_skip_build_and_submit`.

    Args:
        ctx: Active recording context.
        response: Successful completion response from the provider.
        model: Model identifier the provider returned for this call.
        provider: Provider label resolved by the base class.
    """
    await _skip_build_and_submit(
        ctx,
        response.usage,
        model=model,
        provider=provider,
        build=lambda: build_cost_record(ctx, response, model=model, provider=provider),
    )


async def emit_cost_record_from_usage(
    ctx: CostRecordingContext,
    usage: TokenUsage,
    *,
    model: str,
    provider: str,
    finish_reason: FinishReason = FinishReason.STOP,
    call_category: LLMCallCategory | None = None,
) -> None:
    """Build and submit a CostRecord from a bare usage record.

    The counterpart to :func:`emit_cost_record_from_context` for callers
    holding only a :class:`TokenUsage`: the streaming path (terminal
    ``USAGE`` chunk) and the image-generation path (per-image cost).
    Zero-usage and failure-handling semantics mirror the completion path.
    ``call_category`` overrides ``ctx.call_category`` on the record (e.g.
    ``IMAGE_GENERATION``). ``MemoryError`` / ``RecursionError`` propagate.
    """
    await _skip_build_and_submit(
        ctx,
        usage,
        model=model,
        provider=provider,
        build=lambda: build_cost_record_from_usage(
            ctx,
            usage,
            model=model,
            provider=provider,
            finish_reason=finish_reason,
            call_category=call_category,
        ),
    )


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

    Raises:
        CancelledError: Propagated unchanged when the event loop
            cancels this best-effort task during shutdown.
    """
    try:
        await asyncio.wait_for(
            ctx.cost_tracker.record(record),
            timeout=_COST_RECORD_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        _note_cost_failure(
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
            provider=provider,
            model=model,
            error_type=type(exc).__name__,
            timeout_seconds=_COST_RECORD_TIMEOUT_SECONDS,
            reason="cost_tracker_record_timeout",
        )
        return
    except asyncio.CancelledError:
        # Loop shutdown cancelling a pending best-effort cost task is
        # expected; propagate cleanly rather than logging it as a failure.
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        _note_cost_failure(
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
            provider=provider,
            model=model,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            reason="cost_tracker_record_failed",
        )
        return
    _note_cost_success()

    # Only log success after the tracker has actually accepted the
    # record -- emitting at ``create_task`` time would produce a
    # misleading INFO log when the background submission later fails.
    # The record's category, not the ambient scope's: an image call overrides
    # the scope category, so ``ctx.call_category`` would misreport the row.
    effective_category = record.call_category or ctx.call_category
    logger.info(
        PROVIDER_COST_RECORDED,
        agent_id=ctx.agent_id,
        task_id=ctx.task_id,
        provider=provider,
        model=model,
        cost=record.cost,
        currency=ctx.currency,
        call_category=effective_category.value,
        prompt_class_id=record.prompt_class_id,
        success=record.success,
    )


__all__ = [
    "COST_FAILURE_ESCALATION_STREAK",
    "CostRecordingContext",
    "consecutive_cost_failures",
    "cost_recording_scope",
    "current_cost_context",
    "emit_cost_record_from_context",
    "emit_cost_record_from_usage",
    "resolve_currency",
]
