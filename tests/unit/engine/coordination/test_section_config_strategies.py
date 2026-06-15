"""Section-config strategy seams flow into the per-run CoordinationConfig."""

import pytest

from synthorg.engine.coordination.section_config import CoordinationSectionConfig

pytestmark = pytest.mark.unit


class TestSectionConfigStrategyDefaults:
    """Defaults preserve current behaviour (pipeline off, no-op strategies)."""

    def test_middleware_disabled_by_default(self) -> None:
        assert CoordinationSectionConfig().enable_coordination_middleware is False

    def test_replan_strategy_default_noop(self) -> None:
        assert CoordinationSectionConfig().replan_strategy == "noop"

    def test_orchestrator_strategy_default_naive(self) -> None:
        assert CoordinationSectionConfig().orchestrator_strategy == "naive"

    def test_max_delegation_rounds_default_3(self) -> None:
        assert CoordinationSectionConfig().max_delegation_rounds == 3


class TestToCoordinationConfigThreading:
    """to_coordination_config carries the strategy seams to the per-run model."""

    def test_threads_strategy_fields(self) -> None:
        section = CoordinationSectionConfig(
            replan_strategy="magentic",
            orchestrator_strategy="magentic_dynamic",
            max_delegation_rounds=7,
        )
        run = section.to_coordination_config()
        assert run.replan_strategy == "magentic"
        assert run.orchestrator_strategy == "magentic_dynamic"
        assert run.max_delegation_rounds == 7

    def test_defaults_thread_safe_values(self) -> None:
        run = CoordinationSectionConfig().to_coordination_config()
        assert run.replan_strategy == "noop"
        assert run.orchestrator_strategy == "naive"
        assert run.max_delegation_rounds == 3
