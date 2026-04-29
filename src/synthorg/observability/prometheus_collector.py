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
from typing import TYPE_CHECKING, Any

from prometheus_client import CollectorRegistry, Gauge, Info
from prometheus_client import Counter as PromCounter

from synthorg import __version__
from synthorg.budget.billing import billing_period_start
from synthorg.observability import get_logger
from synthorg.observability.events.metrics import (
    METRICS_COLLECTOR_INITIALIZED,
    METRICS_SCRAPE_COMPLETED,
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.prometheus_labels import (
    _LabelSnapshot,
    _snapshot_for_collector,
    is_known_agent_id,
    update_label_snapshot,
)
from synthorg.observability.prometheus_push_metrics import PushMetrics
from synthorg.observability.prometheus_recording import RecordingMixin

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


class PrometheusCollector(RecordingMixin):
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

        # Push-updated metric families live in their own helper so
        # this module stays under the 800-line ceiling. The
        # attributes below alias into ``_push`` to preserve the
        # original public access pattern.
        self._push = PushMetrics(registry=self.registry, prefix=prefix)
        self._provider_tokens = self._push.provider_tokens
        self._provider_cost = self._push.provider_cost
        self._api_request_duration = self._push.api_request_duration
        self._task_runs = self._push.task_runs
        self._task_duration = self._push.task_duration
        self._tool_invocations = self._push.tool_invocations
        self._tool_duration = self._push.tool_duration
        self._audit_chain_appends = self._push.audit_chain_appends
        self._audit_chain_depth = self._push.audit_chain_depth
        self._audit_chain_last_append_ts = self._push.audit_chain_last_append_ts
        self._otlp_export_batches = self._push.otlp_export_batches
        self._otlp_export_dropped = self._push.otlp_export_dropped
        self._escalation_queue_depth = self._push.escalation_queue_depth
        self._agent_identity_changes = self._push.agent_identity_changes
        self._workflow_execution_duration = self._push.workflow_execution_duration
        self._provider_errors = self._push.provider_errors
        self._cache_operations = self._push.cache_operations
        self._api_error_classification = self._push.api_error_classification
        self._client_disconnects = self._push.client_disconnects

        logger.debug(METRICS_COLLECTOR_INITIALIZED, prefix=prefix)

    async def refresh(self, app_state: AppState) -> None:
        """Refresh all gauge values from AppState services.

        Each service query is wrapped individually so a failure in one
        does not prevent other metrics from updating.

        Args:
            app_state: The application state containing service references.
        """
        # Fetch cost snapshots once and share across metrics.
        total_cost: float | None = None
        daily_cost: float | None = None
        billing_cost: float | None = None
        utc_midnight = datetime.now(UTC).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        if app_state.has_cost_tracker:
            try:
                total_cost = await app_state.cost_tracker.get_total_cost()
                daily_cost = await app_state.cost_tracker.get_total_cost(
                    start=utc_midnight,
                )
                tracker = app_state.cost_tracker
                reset_day = (
                    tracker.budget_config.reset_day
                    if tracker.budget_config is not None
                    else 1
                )
                period_start = billing_period_start(
                    reset_day,
                    now=utc_midnight,
                )
                billing_cost = await tracker.get_total_cost(
                    start=period_start,
                )
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.warning(
                    METRICS_SCRAPE_FAILED,
                    component="cost_tracker",
                    exc_info=True,
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
        await self._refresh_agent_cost_metrics(
            app_state,
            agents,
            utc_midnight,
        )
        await self._refresh_task_metrics(app_state)
        logger.debug(METRICS_SCRAPE_COMPLETED)

    async def _rebuild_label_snapshot(
        self,
        app_state: AppState,
        agents: tuple[Any, ...],
    ) -> None:
        """Refresh the snapshot consumed by sync ``validate_*`` helpers.

        Workflow-definition and department fan out in parallel via
        ``asyncio.TaskGroup`` since the queries are independent. Each
        fetch helper returns a sentinel ``None`` on failure (logged
        WARN), and the merge step keeps the previously-seeded value
        for any failed source rather than blanking it. The snapshot
        only flips ``seeded=True`` once at least one successful build
        has produced an agent_ids set, so a transient cold-start
        registry outage doesn't drop the validators out of bootstrap
        with empty allowlists.
        """
        agent_ids = frozenset(str(a.id) for a in agents)
        previous = _snapshot_for_collector()

        async def _fetch_workflow_definitions() -> frozenset[str] | None:
            try:
                persistence = getattr(app_state, "persistence", None)
                wf_repo = getattr(persistence, "workflow_definitions", None)
                if wf_repo is None:
                    return frozenset()
                definitions = await wf_repo.list_definitions()
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.warning(
                    METRICS_SCRAPE_FAILED,
                    component="workflow_definition_repo",
                    exc_info=True,
                )
                return None
            return frozenset(str(d.id) for d in definitions)

        async def _fetch_departments() -> frozenset[str] | None:
            try:
                dept_service = getattr(app_state, "department_service", None)
                if dept_service is None:
                    return frozenset()
                records, _ = await dept_service.list_departments()
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.warning(
                    METRICS_SCRAPE_FAILED,
                    component="department_service",
                    exc_info=True,
                )
                return None
            return frozenset(str(r.name) for r in records)

        async with asyncio.TaskGroup() as tg:
            wf_task = tg.create_task(_fetch_workflow_definitions())
            dept_task = tg.create_task(_fetch_departments())

        wf_ids = wf_task.result()
        dept_ids = dept_task.result()
        # Carry the previous snapshot's value forward for any source
        # that failed; only a successful fetch overwrites. The
        # snapshot only flips to ``seeded=True`` once both sources
        # have produced at least one usable result during this
        # process lifetime (otherwise we'd exit bootstrap with empty
        # allowlists, fail-closing every push-time metric until the
        # next refresh succeeds).
        merged_workflow_ids = (
            wf_ids if wf_ids is not None else previous.workflow_definition_ids
        )
        merged_departments = dept_ids if dept_ids is not None else previous.departments
        # Seed only if every source has at least one successful read
        # (this round OR a prior round). The first round where a
        # source raises keeps ``seeded=False``; the next successful
        # round flips it on.
        seeded = previous.seeded or (wf_ids is not None and dept_ids is not None)

        update_label_snapshot(
            _LabelSnapshot(
                agent_ids=agent_ids,
                workflow_definition_ids=merged_workflow_ids,
                departments=merged_departments,
                seeded=seeded,
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
        if not app_state.has_cost_tracker:
            self._budget_used_percent.set(0.0)
            self._budget_monthly_cost.set(0.0)
            return
        try:
            tracker = app_state.cost_tracker
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            self._budget_used_percent.set(0.0)
            self._budget_monthly_cost.set(0.0)
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="budget",
                exc_info=True,
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
        if not app_state.has_cost_tracker or daily_cost is None:
            self._budget_daily_used_percent.set(0.0)
            return
        try:
            tracker = app_state.cost_tracker
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            self._budget_daily_used_percent.set(0.0)
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="daily_budget",
                exc_info=True,
            )

    async def _refresh_agent_metrics(
        self,
        app_state: AppState,
    ) -> tuple[Any, ...]:
        """Update agent gauges from AgentRegistryService.

        Always clears label series first so disappeared combinations
        drop to zero.  Then returns early if the agent registry is
        unavailable; otherwise queries active agents and aggregates
        counts by ``(status, trust_level)``.

        Args:
            app_state: The application state containing agent registry.

        Returns:
            Tuple of active agent objects (empty tuple if the agent
            registry is unavailable or a service error occurs).
        """
        self._agents_total.clear()
        if not app_state.has_agent_registry:
            return ()
        try:
            agents = await app_state.agent_registry.list_active()
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="agent_registry",
                exc_info=True,
            )
            return ()

    async def _refresh_agent_cost_metrics(
        self,
        app_state: AppState,
        agents: tuple[Any, ...],
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
        if not agents or not app_state.has_cost_tracker:
            return
        try:
            tracker = app_state.cost_tracker
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="agent_cost",
                exc_info=True,
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
        if not app_state.has_task_engine:
            return
        try:
            tasks, _ = await app_state.task_engine.list_tasks()
            counts: Counter[tuple[str, str]] = Counter()
            for task in tasks:
                status = str(task.status)
                agent = str(task.assigned_to) if task.assigned_to else ""
                if agent and not is_known_agent_id(agent):
                    continue
                counts[(status, agent)] += 1
            for (status, agent), count in counts.items():
                self._tasks_total.labels(
                    status=status,
                    agent=agent,
                ).set(count)
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="task_engine",
                exc_info=True,
            )
