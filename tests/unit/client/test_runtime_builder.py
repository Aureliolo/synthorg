"""Unit tests for the client-simulation intake factory and builder.

Covers ``build_intake_strategy`` dispatch (direct / agent / unknown /
agent-missing-collaborators) and ``build_client_simulation_runtime``
(strategy selection from settings, agent fallback, task-engine gating).
"""

import pytest
import structlog

from synthorg.client.config import IntakeConfig
from synthorg.client.factory import UnknownStrategyError, build_intake_strategy
from synthorg.client.models import ClientRequest, TaskRequirement
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.core.enums import Priority, TaskType
from synthorg.core.task import Task
from synthorg.engine.intake.strategies import AgentIntake, DirectIntake
from synthorg.engine.review.stages.internal import InternalReviewStage
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability.events.client import CLIENT_SIMULATION_RUNTIME_WIRED
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.registry import ProviderRegistry
from tests._shared import as_uuid, make_app_state, mock_of

pytestmark = pytest.mark.unit

_TEST_PROJECT = "client-intake"


class TestBuildIntakeStrategy:
    """Dispatch behaviour of ``build_intake_strategy``."""

    def test_direct_strategy(self) -> None:
        task_engine = mock_of[TaskEngine]()
        strategy = build_intake_strategy(
            IntakeConfig(strategy="direct"),
            task_engine=task_engine,
            default_project=_TEST_PROJECT,
        )
        assert isinstance(strategy, DirectIntake)

    async def test_direct_strategy_files_into_default_project(self) -> None:
        task_engine = mock_of[TaskEngine]()
        task_engine.create_task.return_value = Task(
            id=as_uuid("t-1"),
            title="t",
            description="d",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="ops-intake",
            created_by="acme",
        )
        strategy = build_intake_strategy(
            IntakeConfig(strategy="direct"),
            task_engine=task_engine,
            default_project="ops-intake",
        )
        await strategy.process(
            ClientRequest(
                client_id="acme",
                requirement=TaskRequirement(title="t", description="d"),
            )
        )
        created = task_engine.create_task.call_args.args[0]
        assert created.project == "ops-intake"

    async def test_agent_strategy_files_into_default_project(self) -> None:
        task_engine = mock_of[TaskEngine]()
        provider = ScriptedDriver("test-provider")
        strategy = build_intake_strategy(
            IntakeConfig(strategy="agent", model="test-model-001"),
            task_engine=task_engine,
            provider=provider,
            default_project="ops-intake",
        )
        assert isinstance(strategy, AgentIntake)
        assert strategy._project == "ops-intake"

    def test_agent_strategy(self) -> None:
        task_engine = mock_of[TaskEngine]()
        provider = ScriptedDriver("test-provider")
        strategy = build_intake_strategy(
            IntakeConfig(strategy="agent", model="test-model-001"),
            task_engine=task_engine,
            provider=provider,
            default_project=_TEST_PROJECT,
        )
        assert isinstance(strategy, AgentIntake)

    def test_unknown_strategy_raises(self) -> None:
        task_engine = mock_of[TaskEngine]()
        with pytest.raises(UnknownStrategyError):
            build_intake_strategy(
                IntakeConfig(strategy="nonsense"),
                task_engine=task_engine,
                default_project=_TEST_PROJECT,
            )

    def test_agent_without_provider_raises(self) -> None:
        task_engine = mock_of[TaskEngine]()
        with pytest.raises(UnknownStrategyError):
            build_intake_strategy(
                IntakeConfig(strategy="agent", model="test-model-001"),
                task_engine=task_engine,
                default_project=_TEST_PROJECT,
            )

    def test_agent_without_model_raises(self) -> None:
        task_engine = mock_of[TaskEngine]()
        provider = ScriptedDriver("test-provider")
        with pytest.raises(UnknownStrategyError):
            build_intake_strategy(
                IntakeConfig(strategy="agent"),
                task_engine=task_engine,
                provider=provider,
                default_project=_TEST_PROJECT,
            )


class TestBuildClientSimulationRuntime:
    """Boot-wiring behaviour of ``build_client_simulation_runtime``."""

    def test_returns_populated_state_with_direct_default(self) -> None:
        from synthorg.client.runtime_builder import (
            build_client_simulation_runtime,
        )

        task_engine = mock_of[TaskEngine]()
        app_state = make_app_state(task_engine=task_engine)
        state = build_client_simulation_runtime(app_state, env={})
        assert isinstance(state, ClientSimulationState)
        assert state.intake_engine is not None
        assert state.review_pipeline is not None
        assert state.review_pipeline.stage_names == ("internal",)
        assert state.intake_default_project == "client-intake"

    def test_intake_default_project_env_override(self) -> None:
        from synthorg.client.runtime_builder import (
            build_client_simulation_runtime,
        )

        task_engine = mock_of[TaskEngine]()
        app_state = make_app_state(task_engine=task_engine)
        state = build_client_simulation_runtime(
            app_state,
            env={"SYNTHORG_SIMULATIONS_INTAKE_DEFAULT_PROJECT": "ops-intake"},
        )
        assert state.intake_default_project == "ops-intake"

    def test_agent_selected_without_provider_falls_back_to_direct(self) -> None:
        from synthorg.client.runtime_builder import (
            build_client_simulation_runtime,
        )

        task_engine = mock_of[TaskEngine]()
        app_state = make_app_state(task_engine=task_engine)
        with structlog.testing.capture_logs() as cap:
            state = build_client_simulation_runtime(
                app_state,
                env={"SYNTHORG_SIMULATIONS_INTAKE_STRATEGY": "agent"},
            )
        assert state.intake_engine is not None
        assert isinstance(state.intake_engine.strategy, DirectIntake)
        # The degradation must be observable: a WARNING naming the
        # requested vs effective strategy, not a silent swap.
        degrade = [
            e
            for e in cap
            if e.get("event") == CLIENT_SIMULATION_RUNTIME_WIRED
            and e.get("log_level") == "warning"
        ]
        assert degrade, "agent->direct degradation did not emit a WARNING"
        assert degrade[0]["requested_strategy"] == "agent"
        assert degrade[0]["effective_strategy"] == "direct"

    def test_agent_selected_with_provider_uses_agent_intake(self) -> None:
        from synthorg.client.runtime_builder import (
            build_client_simulation_runtime,
        )

        task_engine = mock_of[TaskEngine]()
        provider = ScriptedDriver("test-provider")
        registry = ProviderRegistry({"test-provider": provider})
        app_state = make_app_state(
            task_engine=task_engine,
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

    def test_default_strategy_build_failure_propagates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed *default* strategy build is a real bug: it must not
        be swallowed by the agent->direct degrade path."""
        from synthorg.client import runtime_builder

        def _boom(*_args: object, **_kwargs: object) -> object:
            msg = "forced direct-strategy build failure"
            raise UnknownStrategyError(msg)

        monkeypatch.setattr(runtime_builder, "build_intake_strategy", _boom)
        app_state = make_app_state(task_engine=mock_of[TaskEngine]())
        # Default strategy is "direct"; the except branch must re-raise
        # rather than recurse into a fallback.
        with pytest.raises(UnknownStrategyError):
            runtime_builder.build_client_simulation_runtime(app_state, env={})


def test_internal_stage_name_contract() -> None:
    # The builder asserts on this name; lock it so a rename of the
    # stage cannot silently change the boot pipeline shape.
    assert InternalReviewStage().name == "internal"
