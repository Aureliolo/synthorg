# module-kind: code
"""Auxiliary boot-runtime wiring: coordination metrics + agent-health pipeline.

Extracted from :mod:`synthorg.workers.runtime_builder` (which stays within its
orchestrator size budget). Builds the shared coordination-metrics collector and
the post-run agent-health monitoring pipeline that the provider-present runtime
switch threads into the boot engine and coordinator.
"""

from collections.abc import Awaitable, Callable

from synthorg.api.state import AppState
from synthorg.budget.baseline_store import BaselineStore
from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.budget.state import BudgetStateSlice, cost_tracker_of
from synthorg.communication.state import CommunicationStateSlice
from synthorg.coordination.state import CoordinationStateSlice
from synthorg.engine.health import (
    HealthJudge,
    HealthMonitoringPipeline,
    TriageFilter,
)
from synthorg.notifications.state import NotificationsStateSlice
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import resolve_init_int
from synthorg.settings.state import config_resolver_of

_BASELINE_WINDOW_KEY: str = "baseline_window_size"


def _resolve_baseline_window_size() -> int:
    """Resolve ``budget.baseline_window_size`` at boot.

    Cat-2 boot knob (``read_only_post_init``): the ``BaselineStore``
    sliding window is sized once at construction, so the value is
    sourced env > registered default via the bootstrap resolver (a
    runtime change requires a restart).

    Returns:
        The resolved baseline sliding-window size.
    """
    return resolve_init_int(SettingNamespace.BUDGET, _BASELINE_WINDOW_KEY)


def _construct_coordination_collector(
    app_state: AppState,
) -> CoordinationMetricsCollector | None:
    """Build the shared coordination-metrics collector, or ``None``.

    Requires a live ``CostTracker`` (the collector's only non-optional
    dependency). Without one - the empty/degraded path - no collector
    is built and the metrics pipeline stays a no-op, mirroring the
    ``_construct_agent_engine`` optional-dependency guards. The single
    instance returned is threaded into both the single-agent
    ``AgentEngine`` and the multi-agent coordinator so one
    ``BaselineStore`` accumulates the single-agent baselines the
    multi-agent metrics compare against.

    Returns:
        The shared ``CoordinationMetricsCollector``, or ``None`` when no
        ``CostTracker`` is wired (empty / degraded path).
    """
    if app_state.slice(BudgetStateSlice).cost_tracker is None:
        return None
    baseline_store = BaselineStore(window_size=_resolve_baseline_window_size())
    return CoordinationMetricsCollector(
        config=app_state.config.coordination_metrics,
        cost_tracker=cost_tracker_of(app_state),
        message_bus=app_state.slice(CommunicationStateSlice).message_bus,
        baseline_store=baseline_store,
        metrics_store=app_state.slice(CoordinationStateSlice).metrics_store,
        clock=app_state.clock,
    )


def _build_health_runtime(
    app_state: AppState,
    *,
    quality_degradation_threshold: int,
) -> tuple[HealthMonitoringPipeline | None, Callable[[], Awaitable[bool]] | None]:
    """Build the post-run agent-health pipeline + its live enabled check.

    The pipeline composes the sensitive :class:`HealthJudge`, the
    conservative :class:`TriageFilter`, and the notification dispatcher as
    the escalation sink. Without a wired dispatcher there is nowhere to
    deliver escalations, so ``(None, None)`` is returned. The enabled
    check re-reads ``engine.health_monitoring_enabled`` per run so the
    monitor can be toggled without a restart.

    Args:
        app_state: The live application state.
        quality_degradation_threshold: Bridged
            ``engine.health_quality_degradation_threshold`` (minimum
            consecutive INCORRECT step signals before the judge escalates).

    Returns:
        A ``(pipeline, enabled_check)`` pair, or ``(None, None)`` when no
        notification dispatcher is wired.
    """
    dispatcher = app_state.slice(NotificationsStateSlice).dispatcher
    if dispatcher is None:
        return None, None
    pipeline = HealthMonitoringPipeline(
        judge=HealthJudge(
            quality_degradation_threshold=quality_degradation_threshold,
        ),
        triage=TriageFilter(),
        notification_dispatcher=dispatcher,
    )
    resolver = config_resolver_of(app_state)

    async def _enabled() -> bool:
        return await resolver.get_bool(
            SettingNamespace.ENGINE, "health_monitoring_enabled"
        )

    return pipeline, _enabled
