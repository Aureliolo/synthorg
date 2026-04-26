"""Prometheus metrics collector for SynthOrg business metrics.

Maintains Gauge/Counter instances in a dedicated ``CollectorRegistry``
and refreshes them from AppState services at scrape time.  The
``/metrics`` endpoint calls :meth:`refresh` before generating output.

Coordination metrics (efficiency, overhead) are push-updated by the
coordination collector after each multi-agent execution -- they are
not refreshed on scrape.
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
    API_REQUEST_VALIDATION_FAILED,
    METRICS_COLLECTOR_INITIALIZED,
    METRICS_COORDINATION_RECORDED,
    METRICS_SCRAPE_COMPLETED,
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.prometheus_labels import (
    VALID_API_ERROR_CATEGORIES,
    VALID_AUDIT_APPEND_STATUSES,
    VALID_CACHE_NAMES,
    VALID_CACHE_OUTCOMES,
    VALID_IDENTITY_CHANGE_TYPES,
    VALID_OTLP_KINDS,
    VALID_OTLP_OUTCOMES,
    VALID_PROVIDER_ERROR_CLASSES,
    VALID_STATUS_CLASSES,
    VALID_TASK_OUTCOMES,
    VALID_TOOL_OUTCOMES,
    VALID_VERDICTS,
    VALID_WORKFLOW_EXECUTION_STATUSES,
    require_finite,
    require_label,
    require_non_negative,
    status_class,
)
from synthorg.observability.prometheus_push_metrics import PushMetrics

# Backwards-compatible alias for tests that imported the previous helper.
_status_class = status_class

if TYPE_CHECKING:
    from synthorg.api.state import AppState

logger = get_logger(__name__)


class PrometheusCollector:
    """Collects business metrics from SynthOrg services for Prometheus.

    Uses a dedicated ``CollectorRegistry`` to avoid polluting the global
    default registry.  Most metric values are refreshed on each scrape
    via :meth:`refresh` (pull model).  Coordination metrics are
    push-updated by :meth:`record_coordination_metrics` after each
    multi-agent execution; security verdicts are push-updated by
    :meth:`record_security_verdict`.

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

        logger.debug(METRICS_COLLECTOR_INITIALIZED, prefix=prefix)

    # Backwards-compatible aliases for the bounded label-value sets.
    # The canonical definitions live in ``prometheus_labels`` so the
    # collector module stays below the 800-line limit.
    _VALID_VERDICTS = VALID_VERDICTS

    def record_security_verdict(self, verdict: str) -> None:
        """Increment the security verdict counter.

        Called by a thin hook around ``SecOpsService.evaluate_pre_tool()``.

        Args:
            verdict: The verdict string -- one of ``"allow"``,
                ``"deny"``, ``"escalate"``, or ``"output_scan"``
                (see ``_VALID_VERDICTS``).

        Raises:
            ValueError: If *verdict* is not in the allowed set.
        """
        if verdict not in self._VALID_VERDICTS:
            msg = (
                f"Unknown security verdict {verdict!r}; "
                f"expected one of {sorted(self._VALID_VERDICTS)}"
            )
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="security_verdict",
                verdict=verdict,
                expected=sorted(self._VALID_VERDICTS),
            )
            raise ValueError(msg)
        self._security_evaluations.labels(verdict=verdict).inc()

    def record_provider_usage(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        """Record an LLM provider call's token and cost usage.

        Called from ``integration/provider_caller.py`` after a
        completion resolves (after retry/rate-limit). Tokens and cost
        are monotonically increasing counters -- never reset at
        runtime.

        Args:
            provider: Provider id (e.g. ``"example-provider"``).
            model: Model name (e.g. ``"large"``).
            input_tokens: Tokens in the request prompt.
            output_tokens: Tokens in the response completion.
            cost: Computed cost in the configured currency for this call.
        """
        require_non_negative("record_provider_usage: input_tokens", input_tokens)
        require_non_negative("record_provider_usage: output_tokens", output_tokens)
        require_non_negative("record_provider_usage: cost", cost)
        self._provider_tokens.labels(
            provider=provider,
            model=model,
            direction="input",
        ).inc(input_tokens)
        self._provider_tokens.labels(
            provider=provider,
            model=model,
            direction="output",
        ).inc(output_tokens)
        self._provider_cost.labels(
            provider=provider,
            model=model,
        ).inc(cost)

    def record_api_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_sec: float,
    ) -> None:
        """Record an HTTP request handler's duration.

        Called from ``RequestLoggingMiddleware`` (``api/middleware.py``)
        once the response is fully constructed. ``route`` is a route
        template (e.g. ``"/agents/{agent_id}"``), never a raw path;
        the middleware resolves this via ``scope["route_handler"]``.

        Args:
            method: HTTP method (uppercase, e.g. ``"GET"``).
            route: Route template string; ``"__unmatched__"`` for 404s.
            status_code: Response status code (100-599).
            duration_sec: Wall-clock duration in seconds.
        """
        status_class = _status_class(status_code)
        if status_class not in VALID_STATUS_CLASSES:
            logger.warning(
                API_REQUEST_VALIDATION_FAILED,
                component="api_request",
                reason="invalid_status_code",
                method=method,
                route=route,
                status_code=status_code,
            )
            msg = f"record_api_request: invalid status_code {status_code!r}"
            raise ValueError(msg)
        require_non_negative("record_api_request: duration_sec", duration_sec)
        self._api_request_duration.labels(
            method=method,
            route=route,
            status_class=status_class,
        ).observe(duration_sec)

    def record_task_run(
        self,
        *,
        outcome: str,
        duration_sec: float,
    ) -> None:
        """Record a task's final outcome and runtime.

        Args:
            outcome: One of ``"succeeded"``, ``"failed"``,
                ``"cancelled"``.
            duration_sec: Wall-clock duration in seconds.

        Raises:
            ValueError: If *outcome* is not a valid value or
                ``duration_sec`` is negative.
        """
        require_label("task outcome", outcome, VALID_TASK_OUTCOMES)
        require_non_negative("record_task_run: duration_sec", duration_sec)
        self._task_runs.labels(outcome=outcome).inc()
        self._task_duration.labels(outcome=outcome).observe(duration_sec)

    def record_tool_invocation(
        self,
        *,
        tool_name: str,
        outcome: str,
        duration_sec: float,
    ) -> None:
        """Record a tool invocation's outcome and runtime.

        Args:
            tool_name: Registered tool name (e.g. ``"web_search"``).
            outcome: One of ``"success"``, ``"error"``, ``"timeout"``.
            duration_sec: Wall-clock duration in seconds.

        Raises:
            ValueError: If *outcome* is not a valid value or
                ``duration_sec`` is negative.
        """
        require_label("tool outcome", outcome, VALID_TOOL_OUTCOMES)
        require_non_negative("record_tool_invocation: duration_sec", duration_sec)
        self._tool_invocations.labels(
            tool_name=tool_name,
            outcome=outcome,
        ).inc()
        self._tool_duration.labels(
            tool_name=tool_name,
            outcome=outcome,
        ).observe(duration_sec)

    def record_provider_error(
        self,
        *,
        provider: str,
        model: str,
        error_class: str,
    ) -> None:
        """Increment the provider-error counter for a failed completion.

        Wired from :meth:`BaseCompletionProvider.complete`/``stream``;
        the caller classifies the exception via
        :func:`synthorg.providers.errors.classify_provider_error` so
        ``error_class`` stays bounded.
        """
        require_label("error_class", error_class, VALID_PROVIDER_ERROR_CLASSES)
        self._provider_errors.labels(
            provider=provider,
            model=model,
            error_class=error_class,
        ).inc()

    def record_cache_operation(
        self,
        *,
        cache_name: str,
        outcome: str,
    ) -> None:
        """Increment the in-process cache-operations counter (hit/miss/evict).

        ``cache_name`` bounded by :data:`VALID_CACHE_NAMES`; outcome
        by :data:`VALID_CACHE_OUTCOMES`.  Hit-rate PromQL:
        ``rate(...{outcome="hit"}) / rate(...)`` per ``cache_name``.
        """
        require_label("cache_name", cache_name, VALID_CACHE_NAMES)
        require_label("cache outcome", outcome, VALID_CACHE_OUTCOMES)
        self._cache_operations.labels(
            cache_name=cache_name,
            outcome=outcome,
        ).inc()

    def record_api_error(
        self,
        *,
        category: str,
        status_code: int,
    ) -> None:
        """Increment the API error classification counter (4xx/5xx only).

        ``category`` tracks the RFC 9457 taxonomy
        (:data:`VALID_API_ERROR_CATEGORIES`, mirroring
        :class:`synthorg.api.errors.ErrorCategory`); 2xx/3xx status
        codes are rejected so the counter only covers real failures.
        """
        require_label("api error category", category, VALID_API_ERROR_CATEGORIES)
        sc = _status_class(status_code)
        if sc not in {"4xx", "5xx"}:
            msg = (
                f"record_api_error: status_code {status_code!r} is not 4xx/5xx"
                f" (mapped to {sc!r})"
            )
            raise ValueError(msg)
        self._api_error_classification.labels(
            category=category,
            status_class=sc,
        ).inc()

    def record_audit_append(
        self,
        *,
        status: str,
        chain_depth: int,
        timestamp_unix: float,
    ) -> None:
        """Record an audit chain append event.

        Args:
            status: One of ``"signed"`` (TSA granted), ``"fallback"``
                (local clock), or ``"error"``.
            chain_depth: Hash chain length after the append.
            timestamp_unix: Unix epoch seconds of the append.

        Raises:
            ValueError: If *status* is not a valid value or
                *chain_depth* is negative.
        """
        require_label("audit append status", status, VALID_AUDIT_APPEND_STATUSES)
        require_non_negative("record_audit_append: chain_depth", chain_depth)
        require_finite("record_audit_append: timestamp_unix", timestamp_unix)
        self._audit_chain_appends.labels(status=status).inc()
        self._audit_chain_depth.set(chain_depth)
        self._audit_chain_last_append_ts.set(timestamp_unix)

    def record_otlp_export(
        self,
        *,
        kind: str,
        outcome: str,
        dropped_records: int = 0,
    ) -> None:
        """Record an OTLP export batch outcome.

        Args:
            kind: ``"logs"`` or ``"traces"``.
            outcome: ``"success"`` or ``"failure"``.
            dropped_records: Count of records dropped (queue full or
                retry budget exhausted). Defaults to 0.

        Raises:
            ValueError: If *kind* or *outcome* are invalid or
                *dropped_records* is negative.
        """
        require_label("OTLP kind", kind, VALID_OTLP_KINDS)
        require_label("OTLP outcome", outcome, VALID_OTLP_OUTCOMES)
        require_non_negative("record_otlp_export: dropped_records", dropped_records)
        self._otlp_export_batches.labels(kind=kind, outcome=outcome).inc()
        if dropped_records > 0:
            self._otlp_export_dropped.labels(kind=kind).inc(dropped_records)

    def record_coordination_metrics(
        self,
        *,
        efficiency: float,
        overhead_percent: float,
    ) -> None:
        """Update coordination gauges after a multi-agent execution.

        Called by ``CoordinationCollector`` post-execution.

        Args:
            efficiency: Coordination efficiency ratio (0.0-1.0).
            overhead_percent: Coordination overhead percentage.
        """
        self._coordination_efficiency.set(efficiency)
        self._coordination_overhead_percent.set(overhead_percent)
        logger.debug(
            METRICS_COORDINATION_RECORDED,
            efficiency=efficiency,
            overhead_percent=overhead_percent,
        )

    def record_escalation_queue_depth(
        self,
        *,
        department: str,
        depth: int,
    ) -> None:
        """Set the escalation queue depth gauge for a department.

        Args:
            department: Department name owning the escalation queue.
            depth: Current count of pending escalations.
        """
        if not department:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="escalation_queue_depth",
                reason="empty_department",
            )
            msg = "record_escalation_queue_depth: department must be non-empty"
            raise ValueError(msg)
        require_non_negative("record_escalation_queue_depth: depth", depth)
        self._escalation_queue_depth.labels(department=department).set(depth)

    def record_agent_identity_change(
        self,
        *,
        agent_id: str,
        change_type: str,
    ) -> None:
        """Increment the agent identity change counter.

        ``change_type`` must be one of ``VALID_IDENTITY_CHANGE_TYPES``
        (``"created"``, ``"updated"``, ``"rolled_back"``, ``"archived"``).
        """
        if not agent_id:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="agent_identity_change",
                reason="empty_agent_id",
            )
            msg = "record_agent_identity_change: agent_id must be non-empty"
            raise ValueError(msg)
        require_label(
            "record_agent_identity_change: change_type",
            change_type,
            VALID_IDENTITY_CHANGE_TYPES,
        )
        self._agent_identity_changes.labels(
            agent_id=agent_id,
            change_type=change_type,
        ).inc()

    def record_workflow_execution(
        self,
        *,
        workflow_definition_id: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """Observe a completed workflow execution in the duration histogram.

        ``workflow_definition_id`` must be the stable workflow
        definition id (bounded), NOT a per-run execution id, to keep
        Prometheus cardinality in check.
        """
        if not workflow_definition_id:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="workflow_execution",
                reason="empty_workflow_definition_id",
            )
            msg = "record_workflow_execution: workflow_definition_id must be non-empty"
            raise ValueError(msg)
        require_label(
            "record_workflow_execution: status",
            status,
            VALID_WORKFLOW_EXECUTION_STATUSES,
        )
        require_non_negative(
            "record_workflow_execution: duration_seconds",
            duration_seconds,
        )
        self._workflow_execution_duration.labels(
            workflow_definition_id=workflow_definition_id,
            status=status,
        ).observe(duration_seconds)

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
        await self._refresh_agent_cost_metrics(
            app_state,
            agents,
            utc_midnight,
        )
        await self._refresh_task_metrics(app_state)
        logger.debug(METRICS_SCRAPE_COMPLETED)

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
        """Update task gauges from TaskEngine."""
        self._tasks_total.clear()
        if not app_state.has_task_engine:
            return
        try:
            tasks, _ = await app_state.task_engine.list_tasks()
            counts: Counter[tuple[str, str]] = Counter()
            for task in tasks:
                status = str(task.status)
                agent = str(task.assigned_to) if task.assigned_to else ""
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
