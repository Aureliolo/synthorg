"""Data-source fetchers and timeline assembly for the activity feed.

Pure helper module backing ``ActivityController``: the concurrent
cost/tool/delegation fetchers (each with graceful per-source
degradation), the performance-tracker and currency resolvers, the
exception-group spine walkers used to surface a real cause from a
``TaskGroup`` failure, and ``_build_timeline`` which merges every
source into a single chronological timeline. The controller imports
these as ``from synthorg.api.controllers.activities._shared import ...``.
"""

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.state import BudgetStateSlice
from synthorg.communication.delegation.models import DelegationRecord
from synthorg.communication.state import CommunicationStateSlice
from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.hr.activity import (
    ActivityEvent,
    merge_activity_timeline,
)
from synthorg.hr.performance.models import TaskMetricRecord
from synthorg.hr.state import performance_tracker_of
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_REQUEST_ERROR,
)
from synthorg.settings.state import config_resolver_of
from synthorg.tools.invocation_record import ToolInvocationRecord
from synthorg.tools.state import ToolsStateSlice

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from synthorg.hr.models import AgentLifecycleEvent

logger = get_logger(__name__)


# Degraded source names -- used in responses and tests.
_SRC_PERFORMANCE_TRACKER = "performance_tracker"
_SRC_COST_TRACKER = "cost_tracker"
_SRC_TOOL_INVOCATION_TRACKER = "tool_invocation_tracker"
_SRC_DELEGATION_RECORD_STORE = "delegation_record_store"
_SRC_BUDGET_CONFIG = "budget_config"


def _first_leaf_exception(eg: BaseExceptionGroup[BaseException]) -> BaseException:
    """Return the first non-group leaf exception inside *eg*.

    ``except*`` and ``ExceptionGroup.subgroup`` preserve the original
    nested structure, so ``eg.exceptions[0]`` may itself be another
    ``ExceptionGroup`` (e.g. if the inner ``TaskGroup`` raised a group
    that the outer ``TaskGroup`` re-grouped). Walk the leftmost spine
    until a non-group leaf is reached so the caller always logs and
    re-raises a real cause, not a wrapper group.

    Returns:
        ``BaseException`` instance.
    """
    candidate: BaseException = eg
    while isinstance(candidate, BaseExceptionGroup) and candidate.exceptions:
        candidate = candidate.exceptions[0]
    return candidate


def _leaf_exception_count(eg: BaseExceptionGroup[BaseException]) -> int:
    """Count non-group leaf exceptions across the nested structure of *eg*.

    Returns:
        Resulting integer.
    """
    count = 0
    for child in eg.exceptions:
        if isinstance(child, BaseExceptionGroup):
            count += _leaf_exception_count(child)
        else:
            count += 1
    return count


def _extract_task_result[T](
    task: asyncio.Task[tuple[tuple[T, ...], bool]] | None,
    source_name: str,
    degraded: list[str],
) -> tuple[T, ...]:
    """Extract a completed task's data, appending to degraded if needed.

    Returns:
        Tuple of the declared element types.
    """
    if task is None or task.cancelled():
        degraded.append(source_name)
        return ()
    if task.exception() is not None:
        degraded.append(source_name)
        return ()
    data, is_degraded = task.result()
    if is_degraded:
        degraded.append(source_name)
    return data


async def _run_async_fetchers(
    app_state: AppState,
    agent_id: str | None,
    since: datetime,
    now: datetime,
    degraded: list[str],
) -> tuple[
    tuple[CostRecord, ...],
    tuple[ToolInvocationRecord, ...],
    tuple[DelegationRecord, ...],
    tuple[DelegationRecord, ...],
]:
    """Run cost, tool, and delegation fetchers concurrently.

    Completed tasks have their results extracted; failed or cancelled
    tasks are individually marked as degraded rather than blanket-marking
    all sources.

    Args:
        app_state: Application state with service references.
        agent_id: Optional agent filter.
        since: Start of the time window.
        now: End of the time window.
        degraded: Mutable list to append degraded source names to.

    Returns:
        ``(cost_records, tool_invocations, sent, received)`` tuples.

    Raises:
        fatal_exc: Raised on the corresponding failure path.
        svc_exc: Raised on the corresponding failure path.
    """
    cost_task: asyncio.Task[tuple[tuple[CostRecord, ...], bool]] | None = None
    tool_task: asyncio.Task[tuple[tuple[ToolInvocationRecord, ...], bool]] | None = None
    del_task: (
        asyncio.Task[
            tuple[tuple[DelegationRecord, ...], tuple[DelegationRecord, ...], bool]
        ]
        | None
    ) = None
    try:
        async with asyncio.TaskGroup() as tg:
            cost_task = tg.create_task(
                _fetch_cost_records(app_state, agent_id, since, now),
            )
            tool_task = tg.create_task(
                _fetch_tool_invocations(app_state, agent_id, since, now),
            )
            del_task = tg.create_task(
                _fetch_delegation_records(app_state, agent_id, since, now),
            )
    except* (MemoryError, RecursionError) as fatal_eg:
        fatal_exc = _first_leaf_exception(fatal_eg)
        log_exception_redacted(
            logger,
            API_REQUEST_ERROR,
            fatal_exc,
            endpoint="activities",
            detail="Unable to fetch activity data at this time.",
        )
        raise fatal_exc from fatal_eg
    except* ServiceUnavailableError as svc_eg:
        svc_exc = _first_leaf_exception(svc_eg)
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            detail=(
                "Activity data service is currently unavailable. Please try again."
            ),
            error_type=type(svc_exc).__name__,
            error=safe_error_description(svc_exc),
        )
        raise svc_exc from svc_eg
    except* Exception as other_eg:
        failed_sources = [
            src
            for src, task in [
                (_SRC_COST_TRACKER, cost_task),
                (_SRC_TOOL_INVOCATION_TRACKER, tool_task),
                (_SRC_DELEGATION_RECORD_STORE, del_task),
            ]
            if task is None or task.cancelled() or task.exception() is not None
        ]
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            error_count=_leaf_exception_count(other_eg),
            failed_sources=failed_sources,
        )

    cost_records: tuple[CostRecord, ...] = _extract_task_result(
        cost_task,
        _SRC_COST_TRACKER,
        degraded,
    )
    tool_invocations: tuple[ToolInvocationRecord, ...] = _extract_task_result(
        tool_task,
        _SRC_TOOL_INVOCATION_TRACKER,
        degraded,
    )

    if (
        del_task is not None
        and not del_task.cancelled()
        and del_task.exception() is None
    ):
        del_result = del_task.result()
        sent, received, del_deg = del_result[0], del_result[1], del_result[2]
        if del_deg:
            degraded.append(_SRC_DELEGATION_RECORD_STORE)
    else:
        if del_task is not None:
            degraded.append(_SRC_DELEGATION_RECORD_STORE)
        sent, received = (), ()

    return cost_records, tool_invocations, sent, received


async def _resolve_currency(
    app_state: AppState,
    degraded: list[str],
) -> str:
    """Resolve the display currency from budget config.

    Falls back to ``DEFAULT_CURRENCY`` on any transient error and
    appends the source name to ``degraded``.

    Args:
        app_state: Application state with config resolver.
        degraded: Mutable list to append degraded source names to.

    Returns:
        ISO 4217 currency code.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
    """
    try:
        budget_cfg = await config_resolver_of(app_state).get_budget_config()
    except MemoryError, RecursionError:
        logger.error(
            API_REQUEST_ERROR,
            endpoint="activities",
            source=_SRC_BUDGET_CONFIG,
            detail="Could not load budget configuration; aborting request.",
        )
        raise
    except Exception as exc:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            detail="budget config unavailable, using default currency",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        degraded.append(_SRC_BUDGET_CONFIG)
        return DEFAULT_CURRENCY
    else:
        return budget_cfg.currency


async def _build_timeline(
    app_state: AppState,
    lifecycle_events: tuple[AgentLifecycleEvent, ...],
    agent_id: str | None,
    since: datetime,
    now: datetime,
) -> tuple[tuple[ActivityEvent, ...], list[str]]:
    """Fetch non-lifecycle data sources, merge with lifecycle events.

    Args:
        app_state: Application state with service references.
        lifecycle_events: Pre-fetched lifecycle events.
        agent_id: Optional agent filter.
        since: Start of the time window.
        now: End of the time window (current time).

    Returns:
        ``(timeline, degraded_sources)`` where ``degraded_sources``
        lists the names of data sources that failed.
    """
    degraded: list[str] = []

    task_metrics, tm_degraded = await _fetch_task_metrics(
        app_state,
        agent_id,
        since,
        now,
    )
    if tm_degraded:
        degraded.append(_SRC_PERFORMANCE_TRACKER)

    cost_records, tool_invocations, sent, received = await _run_async_fetchers(
        app_state,
        agent_id,
        since,
        now,
        degraded,
    )

    currency = await _resolve_currency(app_state, degraded)

    timeline = merge_activity_timeline(
        lifecycle_events=lifecycle_events,
        task_metrics=task_metrics,
        cost_records=cost_records,
        tool_invocations=tool_invocations,
        delegation_records_sent=sent,
        delegation_records_received=received,
        currency=currency,
    )
    return timeline, list(dedupe_preserving_order(degraded))


# ── Data source fetchers (graceful degradation) ──────────────────


async def _fetch_task_metrics(
    app_state: AppState,
    agent_id: str | None,
    since: datetime,
    now: datetime,
) -> tuple[tuple[TaskMetricRecord, ...], bool]:
    """Fetch task metrics, falling back to empty on failure.

    The underlying ``PerformanceTracker`` call is synchronous (in-memory),
    but the wrapper is async for consistency with the other fetchers.

    Returns:
        ``(records, is_degraded)`` tuple.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    try:
        return performance_tracker_of(app_state).get_task_metrics(
            agent_id=agent_id,
            since=since,
            until=now,
        ), False
    except MemoryError, RecursionError:
        logger.error(
            API_REQUEST_ERROR,
            endpoint="activities",
            source=_SRC_PERFORMANCE_TRACKER,
            detail="fatal error",
        )
        raise
    except ServiceUnavailableError:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            source=_SRC_PERFORMANCE_TRACKER,
            detail="service unavailable",
        )
        raise
    except Exception:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            error="performance_tracker_unavailable",
        )
        return (), True


async def _fetch_cost_records(
    app_state: AppState,
    agent_id: str | None,
    since: datetime,
    now: datetime,
) -> tuple[tuple[CostRecord, ...], bool]:
    """Fetch cost records, falling back to empty on failure.

    Returns:
        ``(records, is_degraded)`` tuple.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    cost_tracker = app_state.slice(BudgetStateSlice).cost_tracker
    if cost_tracker is None:
        return (), False
    try:
        return await cost_tracker.get_records(
            agent_id=agent_id,
            start=since,
            end=now,
        ), False
    except MemoryError, RecursionError:
        logger.error(
            API_REQUEST_ERROR,
            endpoint="activities",
            source=_SRC_COST_TRACKER,
            detail="fatal error",
        )
        raise
    except ServiceUnavailableError:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            source=_SRC_COST_TRACKER,
            detail="service unavailable",
        )
        raise
    except Exception:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            error="cost_tracker_unavailable",
        )
        return (), True


async def _fetch_tool_invocations(
    app_state: AppState,
    agent_id: str | None,
    since: datetime,
    now: datetime,
) -> tuple[tuple[ToolInvocationRecord, ...], bool]:
    """Fetch tool invocation records, falling back to empty on failure.

    Returns:
        ``(records, is_degraded)`` tuple.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    tracker = app_state.slice(ToolsStateSlice).invocation_tracker
    if tracker is None:
        return (), False
    try:
        return await tracker.get_records(
            agent_id=agent_id,
            start=since,
            end=now,
        ), False
    except MemoryError, RecursionError:
        logger.error(
            API_REQUEST_ERROR,
            endpoint="activities",
            source=_SRC_TOOL_INVOCATION_TRACKER,
            detail="fatal error",
        )
        raise
    except ServiceUnavailableError:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            source=_SRC_TOOL_INVOCATION_TRACKER,
            detail="service unavailable",
        )
        raise
    except Exception:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            error="tool_invocation_tracker_unavailable",
        )
        return (), True


async def _safe_delegation_query(
    coro: Awaitable[tuple[DelegationRecord, ...]],
    error_label: str,
) -> tuple[tuple[DelegationRecord, ...], bool]:
    """Run a delegation store query with graceful degradation.

    Returns:
        ``(records, is_degraded)`` tuple.

    Raises:
        MemoryError: Raised on the corresponding failure path.
        RecursionError: Raised on the corresponding failure path.
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    try:
        return (await coro), False
    except MemoryError, RecursionError:
        logger.error(
            API_REQUEST_ERROR,
            endpoint="activities",
            source=error_label,
            detail="fatal error",
        )
        raise
    except ServiceUnavailableError:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            source=error_label,
            detail="service unavailable",
        )
        raise
    except Exception:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            error=error_label,
        )
        return (), True


async def _fetch_delegation_records(
    app_state: AppState,
    agent_id: str | None,
    since: datetime,
    now: datetime,
) -> tuple[
    tuple[DelegationRecord, ...],
    tuple[DelegationRecord, ...],
    bool,
]:
    """Fetch delegation records (sent + received), falling back to empty.

    Returns:
        ``(sent, received, is_degraded)`` tuple.
    """
    store = app_state.slice(CommunicationStateSlice).delegation_record_store
    if store is None:
        return (), (), False
    if agent_id is None:
        # Org-wide: each record generates both perspectives.
        all_records, degraded = await _safe_delegation_query(
            store.get_all_records(start=since, end=now),
            "delegation_record_store_unavailable",
        )
        return all_records, all_records, degraded

    # Agent-specific: fetch each perspective concurrently so a
    # failure in one does not discard the other.
    async with asyncio.TaskGroup() as tg:
        sent_task = tg.create_task(
            _safe_delegation_query(
                store.get_records_as_delegator(agent_id, start=since, end=now),
                "delegation_delegator_query_failed",
            ),
        )
        recv_task = tg.create_task(
            _safe_delegation_query(
                store.get_records_as_delegatee(agent_id, start=since, end=now),
                "delegation_delegatee_query_failed",
            ),
        )
    sent, sent_deg = sent_task.result()
    received, recv_deg = recv_task.result()
    return sent, received, sent_deg or recv_deg
