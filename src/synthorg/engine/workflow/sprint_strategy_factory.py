"""Resolve a :class:`CeremonySchedulingStrategy` from its enum type.

The eight ceremony strategies all ship stateless (or clock-only)
constructors, so this factory is a pure type -> instance dispatch used
by the :class:`SprintService` when it activates a sprint. ``None`` and
the explicit task-driven type both select the task-driven default. The
mapping is exhaustive over the enum; a member added without a case is
caught by ``test_sprint_strategy_factory`` rather than silently defaulting
in production.
"""

from synthorg.core.clock import Clock
from synthorg.engine.workflow.ceremony_policy import CeremonyStrategyType
from synthorg.engine.workflow.ceremony_strategy import CeremonySchedulingStrategy
from synthorg.engine.workflow.strategies import (
    BudgetDrivenStrategy,
    CalendarStrategy,
    EventDrivenStrategy,
    ExternalTriggerStrategy,
    HybridStrategy,
    MilestoneDrivenStrategy,
    TaskDrivenStrategy,
    ThroughputAdaptiveStrategy,
)


def strategy_for(
    strategy_type: CeremonyStrategyType | None,
    *,
    clock: Clock | None = None,
) -> CeremonySchedulingStrategy:
    """Build the concrete strategy for *strategy_type*.

    Args:
        strategy_type: The resolved ceremony strategy type, or ``None``
            to select the task-driven default.
        clock: Optional Clock seam, threaded only into the
            throughput-adaptive strategy (the sole stateful, time-aware
            implementation).

    Returns:
        A ready-to-use :class:`CeremonySchedulingStrategy`.
    """
    match strategy_type:
        case CeremonyStrategyType.CALENDAR:
            return CalendarStrategy()
        case CeremonyStrategyType.HYBRID:
            return HybridStrategy()
        case CeremonyStrategyType.EVENT_DRIVEN:
            return EventDrivenStrategy()
        case CeremonyStrategyType.BUDGET_DRIVEN:
            return BudgetDrivenStrategy()
        case CeremonyStrategyType.THROUGHPUT_ADAPTIVE:
            return ThroughputAdaptiveStrategy(clock=clock)
        case CeremonyStrategyType.EXTERNAL_TRIGGER:
            return ExternalTriggerStrategy()
        case CeremonyStrategyType.MILESTONE_DRIVEN:
            return MilestoneDrivenStrategy()
        case _:
            # Task-driven type and None both land here as the default.
            return TaskDrivenStrategy()


__all__ = ["strategy_for"]
