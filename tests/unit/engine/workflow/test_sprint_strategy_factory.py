"""Unit tests for the ceremony-strategy factory."""

import pytest

from synthorg.engine.workflow.ceremony_policy import CeremonyStrategyType
from synthorg.engine.workflow.sprint_strategy_factory import strategy_for
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

pytestmark = pytest.mark.unit

_EXPECTED = {
    CeremonyStrategyType.TASK_DRIVEN: TaskDrivenStrategy,
    CeremonyStrategyType.CALENDAR: CalendarStrategy,
    CeremonyStrategyType.HYBRID: HybridStrategy,
    CeremonyStrategyType.EVENT_DRIVEN: EventDrivenStrategy,
    CeremonyStrategyType.BUDGET_DRIVEN: BudgetDrivenStrategy,
    CeremonyStrategyType.THROUGHPUT_ADAPTIVE: ThroughputAdaptiveStrategy,
    CeremonyStrategyType.EXTERNAL_TRIGGER: ExternalTriggerStrategy,
    CeremonyStrategyType.MILESTONE_DRIVEN: MilestoneDrivenStrategy,
}


@pytest.mark.parametrize(("strategy_type", "expected"), list(_EXPECTED.items()))
def test_strategy_for_maps_each_type(
    strategy_type: CeremonyStrategyType, expected: type
) -> None:
    strategy = strategy_for(strategy_type)
    assert isinstance(strategy, expected)
    assert strategy.strategy_type is strategy_type


def test_strategy_for_none_defaults_to_task_driven() -> None:
    assert isinstance(strategy_for(None), TaskDrivenStrategy)
