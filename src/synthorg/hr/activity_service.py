# module-kind: service
"""Agent activity feed service.

Aggregates the multiple activity sources that ``activity.py`` already
knows how to merge (lifecycle events, task metrics, cost records,
tool invocations, delegation records) into a single call per agent.
MCP handlers use this service to shim
``synthorg_agents_get_activity`` without reimplementing the
multi-source merge in the handler layer.

The REST activity controller (``api/controllers/activities.py``)
keeps its own richer implementation because it also owns the
graceful-degradation + role-based cost redaction that are specific
to the HTTP surface. Those concerns are intentionally not mirrored
here; MCP callers are already admin-scoped.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr._activity_validation import (
    validate_pagination,
    validate_request,
    validate_window,
)
from synthorg.hr.activity import ActivityEvent, merge_activity_timeline
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.hr import (
    HR_ACTIVITY_AGENT_FETCHED,
    HR_ACTIVITY_LIFECYCLE_CAP_HIT,
    HR_ACTIVITY_SOURCE_FETCH_FAILED,
)

if TYPE_CHECKING:
    # Cross-package signature types stay TYPE_CHECKING: hoisting
    # ``budget.cost_record`` to runtime triggers the budget<->providers
    # cold-import cycle (providers/__init__ eagerly re-exports
    # BaseCompletionProvider, which imports budget.cost_record back).
    # PEP 649 evaluates these annotations lazily, so the signatures
    # resolve without a runtime import.
    from synthorg.budget.cost_record import CostRecord
    from synthorg.budget.tracker import CostTracker
    from synthorg.communication.delegation.models import DelegationRecord
    from synthorg.communication.delegation.record_store import (
        DelegationRecordStore,
    )
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.persistence_protocol import LifecycleEventRepository
    from synthorg.settings.resolver import ConfigResolver
    from synthorg.tools.invocation_record import ToolInvocationRecord
    from synthorg.tools.invocation_tracker import ToolInvocationTracker

logger = get_logger(__name__)

_DEFAULT_WINDOW_HOURS: int = 168  # 7 days -- matches API controller cap.
# Safety rail for the lifecycle-events fetch so a pathological agent
# (runaway status churn, accidental event replay) cannot swamp the
# merge step. Set well above any expected production load; when the
# cap is hit the service emits ``HR_ACTIVITY_LIFECYCLE_CAP_HIT`` so
# operators know the merged ``total`` is a lower bound for that
# window, and the caller should tighten the window to see more.
_LIFECYCLE_CAP: int = 10_000


def _collect_result[ResultT](
    task: asyncio.Task[ResultT],
    *,
    source: str,
) -> ResultT:
    """Return ``task.result()`` with a fallback source-label log on failure.

    The ``_fetch_*`` helpers already catch non-fatal exceptions and
    return safe defaults, so a completed ``TaskGroup`` should never
    reach this call with a failed task. This wrapper is defence in
    depth for monkeypatched / subclassed helpers that surface
    exceptions via ``.result()`` directly: it preserves the
    ``source=...`` label that would otherwise be dropped by the
    caller-facing exception.

    Returns:
        Result of type ``ResultT``.

    Raises:
        Exception: Raised when the relevant invariant fails.
    """
    try:
        return task.result()
    except Exception as exc:
        log_exception_redacted(
            logger, HR_ACTIVITY_SOURCE_FETCH_FAILED, exc, source=source
        )
        raise


class ActivityFeedService:
    """Aggregates multi-source activity for a single agent.

    Constructor dependencies are injected individually so MCP bootstrap
    can wire whichever sources the current deployment has available.
    The optional sources (cost / tool invocation / delegation /
    config resolver) default to ``None``; missing sources either skip
    (cost / tool / delegation) or fall back to the neutral
    :data:`DEFAULT_CURRENCY` (config resolver).
    """

    __slots__ = (
        "_config_resolver",
        "_cost_tracker",
        "_delegation_store",
        "_lifecycle_repo",
        "_performance_tracker",
        "_tool_invocation_tracker",
    )

    def __init__(  # noqa: PLR0913 -- one optional arg per injected source
        self,
        *,
        performance_tracker: PerformanceTracker,
        lifecycle_repo: LifecycleEventRepository,
        cost_tracker: CostTracker | None = None,
        tool_invocation_tracker: ToolInvocationTracker | None = None,
        delegation_store: DelegationRecordStore | None = None,
        config_resolver: ConfigResolver | None = None,
    ) -> None:
        """Initialize with the required + optional sources."""
        self._performance_tracker = performance_tracker
        self._lifecycle_repo = lifecycle_repo
        self._cost_tracker = cost_tracker
        self._tool_invocation_tracker = tool_invocation_tracker
        self._delegation_store = delegation_store
        self._config_resolver = config_resolver

    async def get_agent_activity(
        self,
        agent_id: NotBlankStr,
        *,
        offset: int,
        limit: int,
        window_hours: int = _DEFAULT_WINDOW_HOURS,
    ) -> tuple[tuple[ActivityEvent, ...], int]:
        """Return a page of activity events for *agent_id* + the total count.

        Events are newest-first (via
        :func:`merge_activity_timeline`). The total reported is the
        length of the merged timeline for the window; lifecycle
        events are capped at :data:`_LIFECYCLE_CAP` for memory
        safety, and hitting that cap is surfaced via
        :data:`HR_ACTIVITY_LIFECYCLE_CAP_HIT` so callers know the
        total is a lower bound for that window.

        Args:
            agent_id: Agent whose activity to fetch.
            offset: Page offset (>= 0).
            limit: Page size (> 0).
            window_hours: Time window in hours; defaults to 168 (7d).

        Returns:
            Tuple of ``(page, total)``.

        Raises:
            ValueError: If ``offset`` is negative, ``limit`` is not
                strictly positive, or ``window_hours`` is outside
                ``[1, _MAX_WINDOW_HOURS]``.
        """
        agent_key = str(agent_id)
        validate_request(
            agent_id=agent_key,
            offset=offset,
            limit=limit,
            window_hours=window_hours,
        )
        now = datetime.now(UTC)
        since = now - timedelta(hours=window_hours)

        lifecycle_events = await self._fetch_lifecycle(agent_key, since, now)
        if len(lifecycle_events) >= _LIFECYCLE_CAP:
            logger.warning(
                HR_ACTIVITY_LIFECYCLE_CAP_HIT,
                agent_id=agent_key,
                cap=_LIFECYCLE_CAP,
                since=since.isoformat(),
                until=now.isoformat(),
                window_hours=window_hours,
            )
        task_metrics = self._fetch_task_metrics(agent_key, since, now)

        async with asyncio.TaskGroup() as tg:
            cost_task = tg.create_task(
                self._fetch_costs(agent_key, since, now),
            )
            tool_task = tg.create_task(
                self._fetch_tools(agent_key, since, now),
            )
            delegation_task = tg.create_task(
                self._fetch_delegations(agent_key, since, now),
            )

        cost_records = _collect_result(cost_task, source="cost_tracker")
        tool_invocations = _collect_result(tool_task, source="tool_tracker")
        sent, received = _collect_result(
            delegation_task,
            source="delegation_store",
        )

        currency = await self._resolve_currency()
        timeline = merge_activity_timeline(
            lifecycle_events=lifecycle_events,
            task_metrics=task_metrics,
            cost_records=cost_records,
            tool_invocations=tool_invocations,
            delegation_records_sent=sent,
            delegation_records_received=received,
            currency=currency,
        )
        total = len(timeline)
        page = timeline[offset : offset + limit]
        logger.info(
            HR_ACTIVITY_AGENT_FETCHED,
            agent_id=agent_key,
            event_count=len(page),
            total=total,
            window_hours=window_hours,
            currency=currency,
        )
        return page, total

    async def list_recent_activity(
        self,
        *,
        project: str | None = None,
        task_id: str | None = None,
        offset: int,
        limit: int,
        window_hours: int = _DEFAULT_WINDOW_HOURS,
    ) -> tuple[tuple[ActivityEvent, ...], int]:
        """Return a page of recent activity not scoped to a single agent.

        Powers the MCP ``synthorg_activities_list`` tool: operators see
        a recent feed across all agents, optionally narrowed to a
        specific ``project`` or ``task_id``. Lifecycle events (which
        are intrinsically agent-scoped) are included only in the
        unfiltered view; filtering by ``task_id`` or ``project`` drops
        lifecycle events automatically since they don't carry those
        identifiers. Delegations are agent-scoped and therefore not
        included by this method.

        Args:
            project: Optional project filter. When set, only events
                whose underlying record has a matching ``project_id``
                are returned.
            task_id: Optional task filter. When set, only events whose
                underlying record has a matching ``task_id`` are
                returned.
            offset: Page offset (>= 0).
            limit: Page size (> 0).
            window_hours: Time window in hours; defaults to 168 (7d).

        Returns:
            Tuple of ``(page, total)``.

        Raises:
            ValueError: If pagination or window inputs are invalid.

        Note:
            ``project`` and ``task_id`` filters are applied in process
            after fetching every record in the window. The sources do
            not currently expose a scoped query interface, so the
            filter step here narrows the merged timeline rather than
            the per-source fetches. Acceptable while window volume
            stays bounded by ``_LIFECYCLE_CAP`` and the configured
            window duration; revisit if the unfiltered fetch dominates
            request latency.
        """
        validate_pagination(offset=offset, limit=limit)
        validate_window(window_hours=window_hours)
        now = datetime.now(UTC)
        since = now - timedelta(hours=window_hours)

        lifecycle_events: tuple = ()  # type: ignore[type-arg]
        # Skip lifecycle when filtering: lifecycle records are
        # agent-scoped without project/task identifiers.
        if project is None and task_id is None:
            try:
                lifecycle_events = await self._lifecycle_repo.list_events(
                    since=since,
                    limit=_LIFECYCLE_CAP,
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    HR_ACTIVITY_SOURCE_FETCH_FAILED,
                    source="lifecycle_repo",
                    since=since.isoformat(),
                    until=now.isoformat(),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                lifecycle_events = ()

        try:
            task_metrics = self._performance_tracker.get_task_metrics(
                since=since,
                until=now,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                HR_ACTIVITY_SOURCE_FETCH_FAILED,
                source="performance_tracker",
                since=since.isoformat(),
                until=now.isoformat(),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            task_metrics = ()
        if task_id is not None:
            task_metrics = tuple(r for r in task_metrics if r.task_id == task_id)
        # TaskMetricRecord has no project field; project filter narrows
        # the cost source instead.

        async with asyncio.TaskGroup() as tg:
            cost_task = tg.create_task(
                self._fetch_costs_unscoped(since, now),
            )
            tool_task = tg.create_task(
                self._fetch_tools_unscoped(since, now),
            )
        cost_records = _collect_result(cost_task, source="cost_tracker")
        tool_invocations = _collect_result(tool_task, source="tool_tracker")

        if task_id is not None:
            cost_records = tuple(r for r in cost_records if r.task_id == task_id)
            tool_invocations = tuple(
                r for r in tool_invocations if getattr(r, "task_id", None) == task_id
            )
        if project is not None:
            # Sources without project attribution have to be excluded
            # entirely when a project filter is set; otherwise
            # ``list_recent_activity(project=...)`` would leak unrelated
            # task / tool events into the merged feed.
            # ``TaskMetricRecord`` carries no project field, so we drop
            # task metrics. ``tool_invocations`` may carry project
            # context; keep only the rows whose ``project_id`` matches.
            task_metrics = ()
            tool_invocations = tuple(
                r for r in tool_invocations if getattr(r, "project_id", None) == project
            )
            cost_records = tuple(r for r in cost_records if r.project_id == project)

        currency = await self._resolve_currency()
        timeline = merge_activity_timeline(
            lifecycle_events=lifecycle_events,
            task_metrics=task_metrics,
            cost_records=cost_records,
            tool_invocations=tool_invocations,
            currency=currency,
        )
        total = len(timeline)
        page = timeline[offset : offset + limit]
        logger.info(
            HR_ACTIVITY_AGENT_FETCHED,
            agent_id=None,
            project=project,
            task_id=task_id,
            event_count=len(page),
            total=total,
            window_hours=window_hours,
            currency=currency,
        )
        return page, total

    async def _fetch_costs_unscoped(
        self,
        since: datetime,
        now: datetime,
    ) -> tuple[CostRecord, ...]:
        """Best-effort no-agent cost fetch.

        Returns:
            Tuple of ``CostRecord``.
        """
        if self._cost_tracker is None:
            return ()
        try:
            return await self._cost_tracker.get_records(start=since, end=now)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                HR_ACTIVITY_SOURCE_FETCH_FAILED,
                source="cost_tracker",
                since=since.isoformat(),
                until=now.isoformat(),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

    async def _fetch_tools_unscoped(
        self,
        since: datetime,
        now: datetime,
    ) -> tuple[ToolInvocationRecord, ...]:
        """Best-effort no-agent tool fetch.

        Returns:
            Tuple of ``ToolInvocationRecord``.
        """
        if self._tool_invocation_tracker is None:
            return ()
        try:
            return await self._tool_invocation_tracker.get_records(
                start=since,
                end=now,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                HR_ACTIVITY_SOURCE_FETCH_FAILED,
                source="tool_invocation_tracker",
                since=since.isoformat(),
                until=now.isoformat(),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

    async def _resolve_currency(self) -> str:
        """Resolve the runtime display currency.

        Flows through the operator-configured ``budget.currency`` via
        :class:`ConfigResolver` (same pattern as
        ``api/controllers/analytics.py`` and the REST activity
        controller), falling back to :data:`DEFAULT_CURRENCY` when no
        resolver was injected or the resolver call fails. A neutral
        fallback is preferable to a hard error here -- activity feed
        reads should not become unavailable just because budget
        config is temporarily unreadable -- but the failure is logged
        so operators can triage it.

        Returns:
            Result of type ``str``.
        """
        if self._config_resolver is None:
            return DEFAULT_CURRENCY
        try:
            budget = await self._config_resolver.get_budget_config()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                HR_ACTIVITY_SOURCE_FETCH_FAILED,
                source="config_resolver.budget",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return DEFAULT_CURRENCY
        return str(budget.currency)

    async def _fetch_lifecycle(
        self,
        agent_id: str,
        since: datetime,
        now: datetime,
    ) -> tuple:  # type: ignore[type-arg]  # AgentLifecycleEvent is TYPE_CHECKING-only
        """Required lifecycle fetch; re-raise with structured context.

        Returns:
            Result of type ``tuple``.

        Raises:
            Exception: Raised when the relevant invariant fails.
        """
        try:
            return await self._lifecycle_repo.list_events(
                agent_id=agent_id,
                since=since,
                limit=_LIFECYCLE_CAP,
            )
        except Exception as exc:
            # Lifecycle is a required source; re-raise so the handler
            # surfaces the failure instead of silently dropping the
            # entire activity window. The log gives operators enough
            # context to triage.
            log_exception_redacted(
                logger,
                HR_ACTIVITY_SOURCE_FETCH_FAILED,
                exc,
                source="lifecycle_repo",
                agent_id=agent_id,
                since=since.isoformat(),
                until=now.isoformat(),
            )
            raise

    def _fetch_task_metrics(
        self,
        agent_id: str,
        since: datetime,
        now: datetime,
    ) -> tuple:  # type: ignore[type-arg]  # TaskMetricRecord is TYPE_CHECKING-only
        """Synchronous task-metrics fetch; re-raise with structured context.

        ``PerformanceTracker.get_task_metrics`` is synchronous (it
        reads from an in-memory rolling window store), so this helper
        is also synchronous. Wrapping it in a helper mirrors the
        structure of the other sources so error handling stays
        uniform.

        Returns:
            Result of type ``tuple``.

        Raises:
            Exception: Raised when the relevant invariant fails.
        """
        try:
            return self._performance_tracker.get_task_metrics(
                agent_id=agent_id,
                since=since,
                until=now,
            )
        except Exception as exc:
            log_exception_redacted(
                logger,
                HR_ACTIVITY_SOURCE_FETCH_FAILED,
                exc,
                source="performance_tracker",
                agent_id=agent_id,
                since=since.isoformat(),
                until=now.isoformat(),
            )
            raise

    async def _fetch_costs(
        self,
        agent_id: str,
        since: datetime,
        now: datetime,
    ) -> tuple[CostRecord, ...]:
        """Best-effort cost record fetch; empty on any failure.

        Wraps the underlying tracker call so a single failing source
        cannot cancel the sibling fetches running in the same
        ``TaskGroup`` (CLAUDE.md async convention for independent
        best-effort workers).

        Returns:
            Tuple of ``CostRecord``.
        """
        if self._cost_tracker is None:
            return ()
        try:
            return await self._cost_tracker.get_records(
                agent_id=agent_id,
                start=since,
                end=now,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Log so operators can distinguish "no records" from
            # "fetch failed"; the caller still gets an empty tuple
            # so the merge proceeds with the remaining sources.
            logger.warning(
                HR_ACTIVITY_SOURCE_FETCH_FAILED,
                source="cost_tracker",
                agent_id=agent_id,
                since=since.isoformat(),
                until=now.isoformat(),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

    async def _fetch_tools(
        self,
        agent_id: str,
        since: datetime,
        now: datetime,
    ) -> tuple[ToolInvocationRecord, ...]:
        """Best-effort tool invocation fetch; empty on any failure.

        Same resilience pattern as :meth:`_fetch_costs`: a single
        tracker failure must not abort the whole activity merge.

        Returns:
            Tuple of ``ToolInvocationRecord``.
        """
        if self._tool_invocation_tracker is None:
            return ()
        try:
            return await self._tool_invocation_tracker.get_records(
                agent_id=agent_id,
                start=since,
                end=now,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                HR_ACTIVITY_SOURCE_FETCH_FAILED,
                source="tool_invocation_tracker",
                agent_id=agent_id,
                since=since.isoformat(),
                until=now.isoformat(),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()

    async def _fetch_delegations(
        self,
        agent_id: str,
        since: datetime,
        now: datetime,
    ) -> tuple[tuple[DelegationRecord, ...], tuple[DelegationRecord, ...]]:
        """Best-effort delegation fetch; returns ``(sent, received)``.

        Both fetches are independent best-effort workers -- neither
        direction's failure should abort the sibling. Wraps each task
        body per the CLAUDE.md async convention.

        Returns:
            Tuple ``(tuple[DelegationRecord, ...], tuple[DelegationRecord, ...])``.
        """
        store = self._delegation_store
        if store is None:
            return (), ()

        async def _safe_delegator() -> tuple[DelegationRecord, ...]:
            """Safe delegator.

            Returns:
                Tuple of ``DelegationRecord``.
            """
            try:
                return await store.get_records_as_delegator(
                    agent_id,
                    start=since,
                    end=now,
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    HR_ACTIVITY_SOURCE_FETCH_FAILED,
                    source="delegation_store.delegator",
                    agent_id=agent_id,
                    since=since.isoformat(),
                    until=now.isoformat(),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return ()

        async def _safe_delegatee() -> tuple[DelegationRecord, ...]:
            """Safe delegatee.

            Returns:
                Tuple of ``DelegationRecord``.
            """
            try:
                return await store.get_records_as_delegatee(
                    agent_id,
                    start=since,
                    end=now,
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    HR_ACTIVITY_SOURCE_FETCH_FAILED,
                    source="delegation_store.delegatee",
                    agent_id=agent_id,
                    since=since.isoformat(),
                    until=now.isoformat(),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return ()

        async with asyncio.TaskGroup() as tg:
            sent_task = tg.create_task(_safe_delegator())
            recv_task = tg.create_task(_safe_delegatee())
        return sent_task.result(), recv_task.result()


__all__ = ["ActivityFeedService"]
