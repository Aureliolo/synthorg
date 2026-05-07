"""Org-wide activity feed controller."""

import asyncio
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable

    from synthorg.hr.models import AgentLifecycleEvent

from litestar import Controller, Request, get
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter

from synthorg.api.dto import PaginatedResponse
from synthorg.api.guards import has_write_role, require_read_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.budget.cost_record import CostRecord  # noqa: TC001
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.communication.delegation.models import DelegationRecord  # noqa: TC001
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.collections import dedupe_preserving_order
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.hr.activity import (
    ActivityEvent,
    merge_activity_timeline,
    redact_cost_events,
)
from synthorg.hr.enums import ActivityEventType  # noqa: TC001
from synthorg.hr.performance.models import TaskMetricRecord  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_ACTIVITY_FEED_QUERIED,
    API_REQUEST_ERROR,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.tools.invocation_record import ToolInvocationRecord  # noqa: TC001

logger = get_logger(__name__)

# Fallback cap applied when no settings resolver is wired in.
_MAX_LIFECYCLE_EVENTS = 10_000

# Module-level log-once guard: during a prolonged settings outage
# this endpoint is queried once per request and would otherwise
# flood the logs with identical fallback warnings (plus traceback).
# The flag is set when we first emit the fallback warning and is
# cleared on the next successful resolution, so a later outage is
# visible again.
_lifecycle_cap_fallback_logged: bool = False


async def _resolve_lifecycle_cap(app_state: AppState) -> int:
    """Resolve the active lifecycle-query cap, falling back to the constant.

    A settings outage or malformed value must not fail the endpoint;
    the fallback constant keeps the DB-side ``LIMIT`` bounded even
    when the resolver is unavailable. Warnings are log-once per run
    of failures (cleared on recovery) to avoid flooding logs during
    a prolonged outage; traceback logging is suppressed for the same
    reason.
    """
    global _lifecycle_cap_fallback_logged  # noqa: PLW0603
    if not app_state.has_config_resolver:
        return _MAX_LIFECYCLE_EVENTS
    try:
        value = await app_state.config_resolver.get_int(
            SettingNamespace.API.value, "max_lifecycle_events_per_query"
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        if not _lifecycle_cap_fallback_logged:
            logger.warning(
                API_REQUEST_ERROR,
                note="failed to resolve max_lifecycle_events_per_query; using fallback",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                cap=_MAX_LIFECYCLE_EVENTS,
            )
            _lifecycle_cap_fallback_logged = True
        return _MAX_LIFECYCLE_EVENTS
    _lifecycle_cap_fallback_logged = False
    return value


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
    """
    candidate: BaseException = eg
    while isinstance(candidate, BaseExceptionGroup) and candidate.exceptions:
        candidate = candidate.exceptions[0]
    return candidate


def _leaf_exception_count(eg: BaseExceptionGroup[BaseException]) -> int:
    """Count non-group leaf exceptions across the nested structure of *eg*."""
    count = 0
    for child in eg.exceptions:
        if isinstance(child, BaseExceptionGroup):
            count += _leaf_exception_count(child)
        else:
            count += 1
    return count


class ActivityWindowHours(IntEnum):
    """Allowed time windows for the activity feed."""

    DAY = 24
    TWO_DAYS = 48
    WEEK = 168


def _extract_task_result(
    task: asyncio.Task[tuple[tuple[Any, ...], bool]] | None,
    source_name: str,
    degraded: list[str],
) -> tuple[Any, ...]:
    """Extract a completed task's data, appending to degraded if needed."""
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
    """
    cost_task: asyncio.Task[tuple[tuple[CostRecord, ...], bool]] | None = None
    tool_task: asyncio.Task[tuple[tuple[ToolInvocationRecord, ...], bool]] | None = None
    del_task: asyncio.Task[tuple[Any, ...]] | None = None
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
        logger.error(
            API_REQUEST_ERROR,
            endpoint="activities",
            detail="Unable to fetch activity data at this time.",
            error_type=type(fatal_exc).__name__,
            error=safe_error_description(fatal_exc),
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
    """
    try:
        budget_cfg = await app_state.config_resolver.get_budget_config()
    except MemoryError, RecursionError:
        logger.error(
            API_REQUEST_ERROR,
            endpoint="activities",
            source=_SRC_BUDGET_CONFIG,
            detail="Could not load budget configuration; aborting request.",
        )
        raise
    except Exception:
        logger.warning(
            API_REQUEST_ERROR,
            endpoint="activities",
            detail="budget config unavailable, using default currency",
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


class ActivityController(Controller):
    """Org-wide activity feed (REST fallback for WebSocket)."""

    path = "/activities"
    tags = ("activities",)
    guards = [require_read_access]  # noqa: RUF012

    @get()
    async def list_activities(  # noqa: PLR0913
        self,
        request: Request[Any, Any, Any],
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
        event_type: Annotated[
            ActivityEventType | None,
            Parameter(
                query="type",
                description="Filter by event_type",
            ),
        ] = None,
        agent_id: Annotated[
            str | None,
            Parameter(
                max_length=128,
                description="Filter by agent_id",
            ),
        ] = None,
        last_n_hours: Annotated[
            ActivityWindowHours,
            Parameter(description="Time window (24, 48, or 168 hours)"),
        ] = ActivityWindowHours.DAY,
    ) -> PaginatedResponse[ActivityEvent]:
        """Return a paginated org-wide activity feed.

        Merges lifecycle events, task metrics, cost records, tool
        invocations, and delegation records into a unified
        chronological timeline, most recent first.  Non-lifecycle
        data sources degrade gracefully when unavailable.

        Args:
            request: Incoming HTTP request (used for role-based redaction).
            state: Application state.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.
            event_type: Filter by ``ActivityEventType`` (e.g. ``"hired"``).
                Invalid values are rejected with 400.
            agent_id: Filter events for a specific agent.
            last_n_hours: Time window in hours (24, 48, or 168).

        Returns:
            Paginated activity events.  The ``degraded_sources`` field
            lists any data sources that failed gracefully.
        """
        app_state: AppState = state.app_state
        now = datetime.now(UTC)
        since = now - timedelta(hours=last_n_hours)
        lifecycle_cap = await _resolve_lifecycle_cap(app_state)

        lifecycle_events = await app_state.persistence.lifecycle_events.list_events(
            agent_id=agent_id,
            since=since,
            limit=lifecycle_cap,
        )

        timeline, degraded = await _build_timeline(
            app_state,
            lifecycle_events,
            agent_id,
            since,
            now,
        )

        if event_type is not None:
            timeline = tuple(e for e in timeline if e.event_type == event_type)

        # Redact cost details unless the user has a write role.
        # Fail-closed: redact by default if auth identity is missing
        # (e.g. misconfigured excluded path, test stub without scope["user"]).
        auth_user = request.scope.get("user")
        if not (
            isinstance(auth_user, AuthenticatedUser) and has_write_role(auth_user.role)
        ):
            timeline = redact_cost_events(timeline)

        page, meta = paginate_cursor(
            timeline,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )

        logger.debug(
            API_ACTIVITY_FEED_QUERIED,
            returned_events=len(page),
            has_more=meta.has_more,
            type_filter=event_type,
            agent_id_filter=agent_id,
            last_n_hours=last_n_hours,
        )

        return PaginatedResponse(
            data=page,
            pagination=meta,
            degraded_sources=tuple(degraded),
        )


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
    """
    try:
        return app_state.performance_tracker.get_task_metrics(
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
    """
    if not app_state.has_cost_tracker:
        return (), False
    try:
        return await app_state.cost_tracker.get_records(
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
    """
    if not app_state.has_tool_invocation_tracker:
        return (), False
    try:
        return await app_state.tool_invocation_tracker.get_records(
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
    if not app_state.has_delegation_record_store:
        return (), (), False
    store = app_state.delegation_record_store
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
