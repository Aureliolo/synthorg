# module-kind: code
"""Who holds the budget config, and how a resolved one reaches all of them.

Four components carry their own ``BudgetConfig``: the state slice every read
goes through, the enforcer that refuses spend, the tracker whose copy decides
which currency a record may be written in and what the summaries and gauges
are computed against, and the cost optimiser that scores every recommendation.
The tracker, the optimiser and the slice are built during construction,
from the code defaults, because persistence is not connected there and no
setting can be read; the enforcer is built later, with the cost dial, once
persistence is up. Both points are before the operator's stored config can
reach them, which is what adoption is for.

Adoption lives here rather than in the settings subscriber because it has two
triggers and must have one implementation: boot is the first pass, and a
settings write is every pass after it. A deployment whose budget was stored
before it started took neither, so its gauges measured against a default no
operator had chosen.
"""

from synthorg.api.state import AppState
from synthorg.budget.config import BudgetConfig
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP

logger = get_logger(__name__)

__all__ = [
    "adopt_budget_config",
    "adopt_resolved_budget_config",
    "resolved_budget_config",
]


def resolved_budget_config(app_state: AppState) -> BudgetConfig:
    """Return the config already in force, or the code default.

    Every boot step that runs AFTER adoption and needs a ``BudgetConfig``
    reads it through here rather than constructing one. Minting a fresh
    ``BudgetConfig()`` downstream is a second answer to a question adoption
    has already settled, and the later writer wins silently: the cost dial
    did exactly that, wiring the default back onto the slice and onto a
    rebuilt enforcer while the gauge captured at adoption went on showing
    the operator's number.

    Args:
        app_state: Application state owning the budget slice.

    Returns:
        The adopted configuration when one is in force, else the defaults.
    """
    return app_state.slice(BudgetStateSlice).budget_config or BudgetConfig()


def adopt_budget_config(app_state: AppState, resolved: BudgetConfig) -> None:
    """Hand *resolved* to the slice and to every component holding a copy.

    Each holder is optional (a harness runs without them) and protocol-typed
    on the slice, so each is offered its setter and skipped when the wired
    implementation does not carry one.

    Args:
        app_state: Application state owning the budget slice.
        resolved: The configuration every holder must now measure against.
    """
    from synthorg.budget.automated_reports import (  # noqa: PLC0415
        AutomatedReportService,
    )
    from synthorg.budget.enforcer import BudgetEnforcer  # noqa: PLC0415
    from synthorg.budget.optimizer import CostOptimizer  # noqa: PLC0415
    from synthorg.budget.tracker import CostTracker  # noqa: PLC0415

    app_state.wire(BudgetStateSlice, budget_config=resolved)
    budget_slice = app_state.slice(BudgetStateSlice)
    enforcer = budget_slice.budget_enforcer
    if isinstance(enforcer, BudgetEnforcer):
        enforcer.set_budget_config(resolved)
    tracker = budget_slice.cost_tracker
    if isinstance(tracker, CostTracker):
        tracker.set_budget_config(resolved)
    optimizer = budget_slice.cost_optimizer
    if isinstance(optimizer, CostOptimizer):
        optimizer.set_budget_config(resolved)
    reports = budget_slice.report_service
    if isinstance(reports, AutomatedReportService):
        reports.set_budget_config(resolved)


async def adopt_resolved_budget_config(app_state: AppState) -> BudgetConfig | None:
    """Resolve the stored budget config and adopt it, or leave boot standing.

    The startup half of the pair: called once persistence and settings are
    available, so what the operator stored is what every surface measures
    against from the first read rather than from the first write.

    Failure-tolerant by design. Refusing to start over an unreadable budget
    would cost the operator the whole deployment, and what stands instead is
    the code default, which is lower than any ceiling an operator would pick:
    the failure mode refuses spend rather than allowing it.

    Args:
        app_state: Application state owning the budget slice and resolver.

    Returns:
        The adopted configuration, or ``None`` when there was no resolver or
        it could not answer.
    """
    from synthorg.settings.state import SettingsStateSlice  # noqa: PLC0415

    resolver = app_state.slice(SettingsStateSlice).config_resolver
    if resolver is None:
        return None
    try:
        resolved = await resolver.get_budget_config()
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APP_STARTUP,
            service="budget_config",
            note="stored budget config unreadable; the boot defaults stand",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    adopt_budget_config(app_state, resolved)
    logger.info(
        API_APP_STARTUP,
        service="budget_config",
        note="adopted the stored budget config on every holder",
    )
    return resolved
