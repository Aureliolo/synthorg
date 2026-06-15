# module-kind: service
"""Prometheus metrics collector for SynthOrg business metrics.

Maintains Gauge/Counter instances in a dedicated ``CollectorRegistry``
and refreshes them from AppState services at scrape time.  The
``/metrics`` endpoint calls :meth:`refresh` before generating output.

Coordination metrics (efficiency, overhead) are push-updated by the
coordination collector after each multi-agent execution -- they are
not refreshed on scrape.

Push-time recording methods (``record_*``) are inherited from
:class:`~synthorg.observability.prometheus_recording.RecordingMixin`;
this module owns construction + the async ``refresh`` pull-path so
the file stays under the 800-line ceiling mandated by ``CLAUDE.md``.
"""

import asyncio
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from prometheus_client import CollectorRegistry, Gauge, Info
from prometheus_client import Counter as PromCounter

from synthorg import __version__
from synthorg.budget.billing import billing_period_start
from synthorg.budget.state import BudgetStateSlice, cost_tracker_of
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.state import EngineStateSlice, task_engine_of
from synthorg.hr.state import HrStateSlice, agent_registry_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability._prometheus_label_fetchers import (
    agent_ids_from_agents,
    fetch_departments,
    fetch_provider_names,
    fetch_tool_names,
    fetch_workflow_definitions,
)
from synthorg.observability.events.metrics import (
    METRICS_COLLECTOR_INITIALIZED,
    METRICS_SCRAPE_COMPLETED,
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.prometheus_labels import (
    _LabelSnapshot,
    _snapshot_for_collector,
    _snapshot_lock_for_collector,
    is_known_agent_id,
    update_label_snapshot,
)
from synthorg.observability.prometheus_push_metrics import PushMetrics
from synthorg.observability.prometheus_recording import RecordingMixin
from synthorg.observability.prometheus_recording_streams import (
    StreamRecordingMixin,
)
from synthorg.persistence.state import PersistenceStateSlice, persistence_of

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)

# Push-updated metric families aliased from ``PushMetrics`` onto private
# ``self._<name>`` attributes (declared on ``_RecordingMetricsBase``) so
# the recording mixins reach them via their original access pattern.
_PUSH_ALIASED_METRICS: tuple[str, ...] = (
    "provider_tokens", "provider_cost", "provider_call_duration",
    "api_request_duration", "task_runs",
    "task_duration", "tool_invocations", "tool_duration", "audit_chain_appends",
    "audit_chain_depth", "audit_chain_last_append_ts", "otlp_export_batches",
    "otlp_export_dropped", "log_sink_events", "escalation_queue_depth",
    "security_audit_log_fill_ratio", "agent_identity_changes",
    "workflow_execution_duration", "provider_errors", "cache_operations",
    "api_error_classification", "client_disconnects", "approval_decisions",
    "autonomy_promotion_decisions",
    "escalation_outcomes", "push_queue_events", "blueprint_instantiations",
    "settings_mutations", "mcp_handler_outcomes", "mcp_handler_duration",
    "budget_query_duration", "audit_chain_verifications", "ws_connection_lifetime",
    "ws_revalidation_outcomes", "ws_active_connections", "pg_pool_size",
    "pg_pool_active_connections", "pg_pool_acquire_duration", "pg_pool_exhausted",
)  # fmt: skip


class PrometheusCollector(RecordingMixin, StreamRecordingMixin):
    """Collects business metrics from SynthOrg services for Prometheus.

    Uses a dedicated ``CollectorRegistry`` to avoid polluting the global
    default registry.  Most metric values are refreshed on each scrape
    via :meth:`refresh` (pull model).  Coordination metrics are
    push-updated by :meth:`record_coordination_metrics` after each
    multi-agent execution; security verdicts are push-updated by
    :meth:`record_security_verdict`.

    Push methods (``record_*``) are inherited from :class:`RecordingMixin`.

    Args:
        prefix: Metric name prefix (default ``"synthorg"``).
    """

    def __init__(self, *, prefix: str = "synthorg") -> None:
        self._prefix = prefix
        self.registry = CollectorRegistry()

        # -- Info --------------------------------------------------------
        self._info = Info(
            f"{prefix}_app",
            "SynthOrg application info",
            registry=self.registry,
        )
        self._info.info({"version": __version__})

        # -- Agent gauges ------------------------------------------------
        self._agents_total = Gauge(
            f"{prefix}_active_agents_total",
            "Number of active agents",
            ["status", "trust_level"],
            registry=self.registry,
        )

        # -- Task gauges -------------------------------------------------
        self._tasks_total = Gauge(
            f"{prefix}_tasks_total",
            "Number of tasks by status and agent",
            ["status", "agent"],
            registry=self.registry,
        )

        # -- Cost gauges -------------------------------------------------
        self._cost_total = Gauge(
            f"{prefix}_cost_total",
            "Total accumulated cost",
            registry=self.registry,
        )

        # -- Budget gauges -----------------------------------------------
        self._budget_used_percent = Gauge(
            f"{prefix}_budget_used_percent",
            "Accumulated cost as percentage of monthly budget limit",
            registry=self.registry,
        )
        self._budget_monthly_cost = Gauge(
            f"{prefix}_budget_monthly_cost",
            "Monthly budget limit in the configured currency",
            registry=self.registry,
        )
        self._budget_daily_used_percent = Gauge(
            f"{prefix}_budget_daily_used_percent",
            "Daily cost as percentage of prorated daily budget",
            registry=self.registry,
        )

        # -- Per-agent cost gauges ---------------------------------------
        self._agent_cost_total = Gauge(
            f"{prefix}_agent_cost_total",
            "Per-agent accumulated cost in the configured currency",
            ["agent_id"],
            registry=self.registry,
        )
        self._agent_budget_used_percent = Gauge(
            f"{prefix}_agent_budget_used_percent",
            "Per-agent daily cost as percentage of per-agent daily limit",
            ["agent_id"],
            registry=self.registry,
        )

        # -- Coordination gauges (push-updated) --------------------------
        self._coordination_efficiency = Gauge(
            f"{prefix}_coordination_efficiency",
            "Coordination efficiency ratio",
            registry=self.registry,
        )
        self._coordination_overhead_percent = Gauge(
            f"{prefix}_coordination_overhead_percent",
            "Coordination overhead percentage",
            registry=self.registry,
        )

        # -- Security counters -------------------------------------------
        self._security_evaluations = PromCounter(
            f"{prefix}_security_evaluations_total",
            "Security evaluation verdicts",
            ["verdict"],
            registry=self.registry,
        )

        # Push-updated metric families live in their own helper so this
        # module stays under its tier cap. Each is aliased onto a private
        # ``self._<name>`` attribute (declared on ``_RecordingMetricsBase``)
        # so the recording mixins keep their original access pattern.
        self._push = PushMetrics(registry=self.registry, prefix=prefix)
        for _alias in _PUSH_ALIASED_METRICS:
            setattr(self, f"_{_alias}", getattr(self._push, _alias))

        # Serialises the whole ``refresh()`` body. The gauge
        # ``.clear()`` + repopulate steps are not individually locked,
        # so two overlapping ``/metrics`` scrapes would otherwise race
        # and emit transiently-zeroed gauges. Per-instance (not the
        # module-level snapshot lock) because the gauges live on this
        # collector's private registry.
        self._refresh_lock = asyncio.Lock()

        logger.debug(METRICS_COLLECTOR_INITIALIZED, prefix=prefix)

    async def refresh(self, app_state: AppState) -> None:
        """Refresh all gauge values from AppState services.

        Each service query is wrapped individually so a failure in one
        does not prevent other metrics from updating. The whole body
        runs under ``_refresh_lock`` so overlapping ``/metrics`` scrapes
        serialise instead of racing on the gauge clear+repopulate steps.

        Args:
            app_state: The application state containing service references.
        """
        # Lock ordering: ``_refresh_lock`` is always acquired BEFORE the
        # module-level ``_snapshot_lock`` (taken inside
        # ``_merge_and_update_snapshot``). No code path acquires
        # ``_snapshot_lock`` first and then calls ``refresh()``, so this
        # nesting cannot deadlock.
        async with self._refresh_lock:
            await self._refresh_all(app_state)

    async def _fetch_cost_snapshots(
        self,
        app_state: AppState,
        utc_midnight: datetime,
    ) -> tuple[float | None, float | None, float | None]:
        """Fetch total / daily / billing-period cost once for this scrape.

        Returns:
            ``(total_cost, daily_cost, billing_cost)``; any element is
            ``None`` when the cost tracker is absent or the fetch failed
            (logged as a redacted ``METRICS_SCRAPE_FAILED`` warning).
        """
        if app_state.slice(BudgetStateSlice).cost_tracker is None:
            return None, None, None
        try:
            tracker = cost_tracker_of(app_state)
            total_cost = await tracker.get_total_cost()
            daily_cost = await tracker.get_total_cost(start=utc_midnight)
            reset_day = (
                tracker.budget_config.reset_day
                if tracker.budget_config is not None
                else 1
            )
            period_start = billing_period_start(reset_day, now=utc_midnight)
            billing_cost = await tracker.get_total_cost(start=period_start)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="cost_tracker",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None, None, None
        return total_cost, daily_cost, billing_cost

    async def _refresh_all(self, app_state: AppState) -> None:
        """Refresh every gauge once. Caller holds ``_refresh_lock``."""
        utc_midnight = datetime.now(UTC).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        total_cost, daily_cost, billing_cost = await self._fetch_cost_snapshots(
            app_state,
            utc_midnight,
        )
        self._refresh_cost_gauge(total_cost)
        self._refresh_budget_metrics(app_state, billing_cost)
        self._refresh_daily_budget_metric(app_state, daily_cost, utc_midnight)
        agents = await self._refresh_agent_metrics(app_state)
        # Snapshot must seed BEFORE any downstream loop that consults
        # ``is_known_agent_id`` / ``validate_*`` so the freshly-fetched
        # registry data is the basis for label validation in the same
        # scrape.
        await self._rebuild_label_snapshot(app_state, agents)
        # Skip cost-metric rebuild for this scrape if the agent
        # registry fetch failed (``agents is None``) so per-agent
        # gauges keep their prior values rather than zeroing out.
        if agents is not None:
            await self._refresh_agent_cost_metrics(
                app_state,
                agents,
                utc_midnight,
            )
        await self._refresh_task_metrics(app_state)
        self._refresh_pg_pool_metrics(app_state)
        logger.debug(METRICS_SCRAPE_COMPLETED)

    def _refresh_pg_pool_metrics(self, app_state: AppState) -> None:
        """Push Postgres pool size / active gauges from the live pool.

        Skipped silently when the backend is not Postgres or is not
        yet connected; the pool's ``get_stats`` snapshot is the
        authoritative source for ``pool_size`` and ``pool_available``.
        """
        if app_state.slice(PersistenceStateSlice).backend is None:
            return
        backend = persistence_of(app_state)
        if backend.kind != "postgres":
            return
        pool = getattr(backend, "_pool", None)
        if pool is None:
            return
        try:
            # ``psycopg_pool`` ``get_stats()`` returns a copied counters
            # dict under a brief internal lock; it does no I/O and is
            # O(1), so calling it synchronously from this async path does
            # not stall the event loop. Kept inline (rather than
            # ``asyncio.to_thread``) to avoid a thread hop per scrape.
            stats = pool.get_stats()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="pg_pool_stats",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        size = stats.get("pool_size")
        available = stats.get("pool_available")
        if isinstance(size, int):
            self.record_pg_pool_size(backend="primary", size=size)
            if isinstance(available, int):
                self.record_pg_pool_active(
                    backend="primary",
                    active=max(0, size - available),
                )

    async def _rebuild_label_snapshot(
        self,
        app_state: AppState,
        agents: tuple[AgentIdentity, ...] | None,
    ) -> None:
        """Refresh the snapshot consumed by sync ``validate_*`` helpers.

        Orchestrates three steps that each live in their own helper so
        this body stays under the 50-line ceiling: derive
        ``agent_ids``, fan out the workflow + department fetches in
        parallel, then merge under the process-global snapshot lock.
        Each source's ``*_seeded`` flag flips ``True`` the first time
        *its own* source produces a usable result and stays ``True``
        thereafter, so a transient outage in one registry does not
        suppress the unrelated allowlists.
        """
        agent_ids = agent_ids_from_agents(agents)
        provider_names = fetch_provider_names(app_state)
        async with asyncio.TaskGroup() as tg:
            wf_task = tg.create_task(fetch_workflow_definitions(app_state))
            dept_task = tg.create_task(fetch_departments(app_state))
            tool_task = tg.create_task(fetch_tool_names(app_state))
        await self._merge_and_update_snapshot(
            agent_ids=agent_ids,
            wf_ids=wf_task.result(),
            dept_ids=dept_task.result(),
            tool_names=tool_task.result(),
            provider_names=provider_names,
        )

    @staticmethod
    async def _merge_and_update_snapshot(
        *,
        agent_ids: frozenset[str] | None,
        wf_ids: frozenset[str] | None,
        dept_ids: frozenset[str] | None,
        tool_names: frozenset[str] | None,
        provider_names: frozenset[str] | None,
    ) -> None:
        """Merge with the previous snapshot and atomically rebind.

        Runs under the process-global ``_snapshot_lock`` (lives next
        to ``_snapshot`` in ``prometheus_labels``) so two overlapping
        refreshes -- including refreshes from distinct
        ``PrometheusCollector`` instances during tests -- cannot
        interleave their fetches with one another's update and
        clobber a partial-failure carry-forward. The fetch step in
        :meth:`_rebuild_label_snapshot` is deliberately outside the
        lock so a slow registry call does not block other refresh
        work; only this tiny merge-and-rebind step is serialized.
        """
        async with _snapshot_lock_for_collector():
            previous = _snapshot_for_collector()
            # Carry the previous snapshot's value forward for any
            # source that failed; only a successful fetch overwrites.
            merged_agent_ids = (
                agent_ids if agent_ids is not None else previous.agent_ids
            )
            merged_workflow_ids = (
                wf_ids if wf_ids is not None else previous.workflow_definition_ids
            )
            merged_departments = (
                dept_ids if dept_ids is not None else previous.departments
            )
            merged_tool_names = (
                tool_names if tool_names is not None else previous.tool_names
            )
            merged_providers = (
                provider_names if provider_names is not None else previous.providers
            )
            update_label_snapshot(
                _LabelSnapshot(
                    agent_ids=merged_agent_ids,
                    workflow_definition_ids=merged_workflow_ids,
                    departments=merged_departments,
                    tool_names=merged_tool_names,
                    providers=merged_providers,
                    agent_ids_seeded=previous.agent_ids_seeded
                    or (agent_ids is not None),
                    workflow_definition_ids_seeded=(
                        previous.workflow_definition_ids_seeded or (wf_ids is not None)
                    ),
                    departments_seeded=previous.departments_seeded
                    or (dept_ids is not None),
                    tool_names_seeded=previous.tool_names_seeded
                    or (tool_names is not None),
                    providers_seeded=previous.providers_seeded
                    or (provider_names is not None),
                ),
            )

    def _refresh_cost_gauge(self, total_cost: float | None) -> None:
        """Update cost gauge from a pre-fetched total."""
        if total_cost is not None:
            self._cost_total.set(total_cost)

    def _refresh_budget_metrics(
        self,
        app_state: AppState,
        billing_cost: float | None,
    ) -> None:
        """Update budget utilization gauges from CostTracker config.

        Args:
            app_state: The application state containing cost tracker.
            billing_cost: Cost accumulated since the start of the
                current billing period (month start), or ``None``
                if unavailable.
        """
        if app_state.slice(BudgetStateSlice).cost_tracker is None:
            self._budget_used_percent.set(0.0)
            self._budget_monthly_cost.set(0.0)
            return
        try:
            tracker = cost_tracker_of(app_state)
            if tracker.budget_config is None:
                self._budget_used_percent.set(0.0)
                self._budget_monthly_cost.set(0.0)
                return
            monthly = tracker.budget_config.total_monthly
            self._budget_monthly_cost.set(monthly)
            if monthly > 0 and billing_cost is not None:
                self._budget_used_percent.set(
                    min(100.0, (billing_cost / monthly) * 100.0),
                )
            else:
                self._budget_used_percent.set(0.0)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            self._budget_used_percent.set(0.0)
            self._budget_monthly_cost.set(0.0)
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="budget",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _refresh_daily_budget_metric(
        self,
        app_state: AppState,
        daily_cost: float | None,
        utc_midnight: datetime,
    ) -> None:
        """Update daily budget utilization gauge.

        Computes ``daily_cost / (total_monthly / days_in_period) * 100``,
        capped at 100%, where *days_in_period* is the length of the
        current billing period (derived from ``BudgetConfig.reset_day``).
        Resets the gauge to 0.0 if cost tracker is unavailable,
        *daily_cost* is ``None``, budget config is missing, or the
        monthly budget is zero or negative.

        Args:
            app_state: The application state containing cost tracker.
            daily_cost: Cost accumulated since UTC midnight, or ``None``
                if unavailable.
            utc_midnight: Start of the current UTC day, used to derive
                the billing period boundaries for prorated budget.
        """
        if app_state.slice(BudgetStateSlice).cost_tracker is None or daily_cost is None:
            self._budget_daily_used_percent.set(0.0)
            return
        try:
            tracker = cost_tracker_of(app_state)
            if tracker.budget_config is None:
                self._budget_daily_used_percent.set(0.0)
                return
            monthly = tracker.budget_config.total_monthly
            if monthly <= 0:
                self._budget_daily_used_percent.set(0.0)
                return
            reset_day = tracker.budget_config.reset_day
            period_start = billing_period_start(
                reset_day,
                now=utc_midnight,
            )
            if period_start.month == 12:  # noqa: PLR2004
                next_start = period_start.replace(
                    year=period_start.year + 1,
                    month=1,
                )
            else:
                next_start = period_start.replace(
                    month=period_start.month + 1,
                )
            days_in_period = (next_start - period_start).days
            daily_budget = monthly / days_in_period
            self._budget_daily_used_percent.set(
                min(100.0, (daily_cost / daily_budget) * 100.0),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            self._budget_daily_used_percent.set(0.0)
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="daily_budget",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _refresh_agent_metrics(
        self,
        app_state: AppState,
    ) -> tuple[AgentIdentity, ...] | None:
        """Update agent gauges from AgentRegistryService.

        Always clears label series first so disappeared combinations
        drop to zero.  Then returns the empty tuple if the agent
        registry is unavailable; otherwise queries active agents and
        aggregates counts by ``(status, trust_level)``.

        Args:
            app_state: The application state containing agent registry.

        Returns:
            On success: a (possibly empty) tuple of active agent
            objects. The empty tuple is also returned when
            ``app_state.has_agent_registry`` is ``False`` (the
            registry was never wired up); both cases mean "no agents
            to report" and are safe to feed into the snapshot
            rebuild.

            On registry-fetch failure: ``None``. The caller keeps
            the previous label snapshot's agent_ids rather than
            blanking the allowlist, matching the behaviour of the
            workflow / department fetchers in
            :meth:`_rebuild_label_snapshot`.
        """
        if app_state.slice(HrStateSlice).agent_registry is None:
            # No registry means there are no agents to report; clear
            # so a previously-populated gauge family doesn't keep
            # phantom labels alive after the registry is removed.
            self._agents_total.clear()
            return ()
        try:
            agents = await agent_registry_of(app_state).list_active()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # Keep the prior gauge values intact so the dashboard
            # doesn't drop to "0 active agents" on a transient
            # registry-fetch failure. The snapshot path also
            # carries the previous agent_ids forward in this case.
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="agent_registry",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        # Successful fetch: clear stale labels first, then re-set.
        self._agents_total.clear()
        counts: Counter[tuple[str, str]] = Counter()
        for agent in agents:
            status = str(agent.status)
            trust = str(agent.tools.access_level)
            counts[(status, trust)] += 1
        for (status, trust), count in counts.items():
            self._agents_total.labels(
                status=status,
                trust_level=trust,
            ).set(count)
        return tuple(agents)

    async def _refresh_agent_cost_metrics(
        self,
        app_state: AppState,
        agents: tuple[AgentIdentity, ...],
        utc_midnight: datetime,
    ) -> None:
        """Update per-agent cost and budget utilization gauges.

        Always clears gauge label series first so disappeared agents
        are dropped.  Then returns early if *agents* is empty or the
        cost tracker is unavailable; otherwise queries cumulative and
        daily costs per agent.

        Args:
            app_state: The application state containing cost tracker.
            agents: Pre-fetched active agents from the agent registry.
            utc_midnight: Start of the current UTC day for daily cost
                queries.
        """
        self._agent_cost_total.clear()
        self._agent_budget_used_percent.clear()
        if not agents or app_state.slice(BudgetStateSlice).cost_tracker is None:
            return
        try:
            tracker = cost_tracker_of(app_state)
            budget_cfg = tracker.budget_config
            per_agent_limit = (
                budget_cfg.per_agent_daily_limit
                if budget_cfg is not None and budget_cfg.total_monthly > 0
                else 0.0
            )
            agent_ids = [str(a.id) for a in agents]
            # Fan-out cost queries in parallel.
            total_tasks: dict[str, asyncio.Task[float]] = {}
            daily_tasks: dict[str, asyncio.Task[float]] = {}
            async with asyncio.TaskGroup() as tg:
                for aid in agent_ids:
                    total_tasks[aid] = tg.create_task(
                        tracker.get_agent_cost(aid),
                    )
                    if per_agent_limit > 0:
                        daily_tasks[aid] = tg.create_task(
                            tracker.get_agent_cost(
                                aid,
                                start=utc_midnight,
                            ),
                        )
            for aid in agent_ids:
                self._agent_cost_total.labels(agent_id=aid).set(
                    total_tasks[aid].result(),
                )
                if per_agent_limit > 0:
                    daily = daily_tasks[aid].result()
                    pct = min(
                        100.0,
                        (daily / per_agent_limit) * 100.0,
                    )
                    self._agent_budget_used_percent.labels(
                        agent_id=aid,
                    ).set(pct)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="agent_cost",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _refresh_task_metrics(self, app_state: AppState) -> None:
        """Update task gauges from TaskEngine.

        Tasks whose ``assigned_to`` references an agent that is no
        longer in the live registry snapshot are dropped from the
        gauge: keeping them would inflate the ``agent`` label
        cardinality with orphan ids forever. Unassigned tasks are
        preserved under the empty-string label as before.
        """
        self._tasks_total.clear()
        if app_state.slice(EngineStateSlice).task_engine is None:
            return
        try:
            tasks, _ = await task_engine_of(app_state).list_tasks()
            counts: Counter[tuple[str, str]] = Counter()
            # De-duplicate the orphan-agent WARN per scrape: many
            # tasks can share the same stale ``assigned_to``, and
            # logging once per task (instead of once per agent id)
            # would breach the "single WARN per unknown value per
            # scrape" contract documented in monitoring.md.
            warned_unknown_agents: set[str] = set()
            for task in tasks:
                status = str(task.status)
                agent = str(task.assigned_to) if task.assigned_to else ""
                if agent and not is_known_agent_id(agent):
                    if agent not in warned_unknown_agents:
                        warned_unknown_agents.add(agent)
                        # Surface dropped samples so an operator can
                        # spot an orphan ``task.assigned_to`` ref
                        # instead of seeing a silently
                        # lower-than-expected ``synthorg_tasks_total``
                        # for that status. ``task_id`` is the FIRST
                        # task that hit this orphan; the WARN is keyed
                        # by ``rejected_value`` so subsequent tasks
                        # with the same stale agent are silently
                        # dropped.
                        logger.warning(
                            METRICS_SCRAPE_FAILED,
                            component="task_metrics",
                            reason="unknown_agent_id",
                            rejected_value=agent,
                            task_id=str(task.id),
                            task_status=status,
                        )
                    continue
                counts[(status, agent)] += 1
            for (status, agent), count in counts.items():
                self._tasks_total.labels(
                    status=status,
                    agent=agent,
                ).set(count)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="task_engine",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
