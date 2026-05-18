"""Unit tests for the client-simulation intake factory and builder.

Covers ``build_intake_strategy`` dispatch (direct / agent / unknown /
agent-missing-collaborators) and ``build_client_simulation_runtime``
(strategy selection from settings, agent fallback, task-engine gating).
"""

import pytest

from synthorg.client.config import IntakeConfig
from synthorg.client.factory import UnknownStrategyError, build_intake_strategy
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.engine.intake.strategies import AgentIntake, DirectIntake
from synthorg.engine.review.stages.internal import InternalReviewStage
from synthorg.engine.task_engine import TaskEngine
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.registry import ProviderRegistry
from tests._shared import mock_of

pytestmark = pytest.mark.unit


class TestBuildIntakeStrategy:
    """Dispatch behaviour of ``build_intake_strategy``."""

    def test_direct_strategy(self) -> None:
        task_engine = mock_of[TaskEngine]()
        strategy = build_intake_strategy(
            IntakeConfig(strategy="direct"),
            task_engine=task_engine,
        )
        assert isinstance(strategy, DirectIntake)

    def test_agent_strategy(self) -> None:
        task_engine = mock_of[TaskEngine]()
        provider = ScriptedDriver("test-provider")
        strategy = build_intake_strategy(
            IntakeConfig(strategy="agent", model="test-model-001"),
            task_engine=task_engine,
            provider=provider,
        )
        assert isinstance(strategy, AgentIntake)

    def test_unknown_strategy_raises(self) -> None:
        task_engine = mock_of[TaskEngine]()
        with pytest.raises(UnknownStrategyError):
            build_intake_strategy(
                IntakeConfig(strategy="nonsense"),
                task_engine=task_engine,
            )

    def test_agent_without_provider_raises(self) -> None:
        task_engine = mock_of[TaskEngine]()
        with pytest.raises(UnknownStrategyError):
            build_intake_strategy(
                IntakeConfig(strategy="agent", model="test-model-001"),
                task_engine=task_engine,
            )

    def test_agent_without_model_raises(self) -> None:
        task_engine = mock_of[TaskEngine]()
        provider = ScriptedDriver("test-provider")
        with pytest.raises(UnknownStrategyError):
            build_intake_strategy(
                IntakeConfig(strategy="agent"),
                task_engine=task_engine,
                provider=provider,
            )


class TestBuildClientSimulationRuntime:
    """Boot-wiring behaviour of ``build_client_simulation_runtime``."""

    def test_returns_populated_state_with_direct_default(self) -> None:
        from synthorg.api.state import AppState
        from synthorg.client.runtime_builder import (
            build_client_simulation_runtime,
        )

        task_engine = mock_of[TaskEngine]()
        app_state = mock_of[AppState](
            task_engine=task_engine,
            has_task_engine=True,
            has_active_provider=False,
        )
        state = build_client_simulation_runtime(app_state, env={})
        assert isinstance(state, ClientSimulationState)
        assert state.intake_engine is not None
        assert state.review_pipeline is not None
        assert state.review_pipeline.stage_names == ("internal",)

    def test_agent_selected_without_provider_falls_back_to_direct(self) -> None:
        from synthorg.api.state import AppState
        from synthorg.client.runtime_builder import (
            build_client_simulation_runtime,
        )

        task_engine = mock_of[TaskEngine]()
        app_state = mock_of[AppState](
            task_engine=task_engine,
            has_task_engine=True,
            has_active_provider=False,
        )
        state = build_client_simulation_runtime(
            app_state,
            env={"SYNTHORG_SIMULATIONS_INTAKE_STRATEGY": "agent"},
        )
        assert state.intake_engine is not None
        assert isinstance(state.intake_engine.strategy, DirectIntake)

    def test_agent_selected_with_provider_uses_agent_intake(self) -> None:
        from synthorg.api.state import AppState
        from synthorg.client.runtime_builder import (
            build_client_simulation_runtime,
        )

        task_engine = mock_of[TaskEngine]()
        provider = ScriptedDriver("test-provider")
        registry = ProviderRegistry({"test-provider": provider})
        app_state = mock_of[AppState](
            task_engine=task_engine,
            has_task_engine=True,
            has_active_provider=True,
            provider_registry=registry,
        )
        state = build_client_simulation_runtime(
            app_state,
            env={
                "SYNTHORG_SIMULATIONS_INTAKE_STRATEGY": "agent",
                "SYNTHORG_SIMULATIONS_INTAKE_MODEL": "test-model-001",
            },
        )
        assert state.intake_engine is not None
        assert isinstance(state.intake_engine.strategy, AgentIntake)


def test_internal_stage_name_contract() -> None:
    # The builder asserts on this name; lock it so a rename of the
    # stage cannot silently change the boot pipeline shape.
    assert InternalReviewStage().name == "internal"
