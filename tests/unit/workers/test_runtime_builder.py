"""Unit tests for the provider-present runtime-services switch."""

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest

from synthorg.api.state import AppState
from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker import CostTracker
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.bridge_configs import EngineBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
)
from synthorg.workers.runtime_builder import (
    RuntimeServices,
    build_runtime_services,
)
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit


class _AcceptingIntakeStrategy:
    """Deterministic intake strategy: always accepts with a stub task id."""

    async def process(self, request: object) -> IntakeResult:
        request_id = getattr(request, "request_id", "req-x")
        return IntakeResult.accepted_result(
            request_id=str(request_id),
            task_id="task-x",
        )


async def _get_str(_namespace: str, key: str) -> str:
    """Key-aware ``config_resolver.get_str`` stub.

    ``routing_policy`` selects the work pipeline policy; every other
    key (``decomposition_model``) yields a model id.
    """
    if key == "routing_policy":
        return "leaf-threshold"
    return "example-medium-001"


def _provider_app_state(  # noqa: PLR0913 -- test builder with keyword-only knobs
    registry: ProviderRegistry,
    workspace: Path,
    *,
    bridge_config_error: Exception | None = None,
    cost_tracker: CostTracker | None = None,
    coordination_metrics_store: CoordinationMetricsStore | None = None,
    simulation_runtime: bool = False,
) -> AppState:
    """Build a mocked AppState for the provider-present path.

    ``bridge_config_error`` makes ``get_engine_bridge_config`` raise, to
    exercise the fail-open routing-scorer-config resolve branch.
    ``cost_tracker`` (and the paired ``coordination_metrics_store``)
    drive the coordination-metrics collector wiring: absent, the
    collector is not constructed (mirrors the empty/degraded path).
    """
    if bridge_config_error is None:
        bridge_mock = AsyncMock(return_value=EngineBridgeConfig())
    else:
        bridge_mock = AsyncMock(side_effect=bridge_config_error)
    # ``mock_of[T](...)`` is ``Any`` by design; cast back to the spec so
    # the helper keeps a precise signature for its callers.
    return cast(
        "AppState",
        mock_of[AppState](
            has_active_provider=True,
            provider_registry=registry,
            config=RootConfig(company_name="test-corp"),
            config_resolver=mock_of[ConfigResolver](
                get_float=AsyncMock(return_value=30.0),
                get_str=AsyncMock(side_effect=_get_str),
                get_int=AsyncMock(return_value=1),
                get_engine_bridge_config=bridge_mock,
            ),
            task_engine=mock_of[TaskEngine](),
            agent_registry=AgentRegistryService(),
            approval_store=None,
            has_simulation_runtime=simulation_runtime,
            client_simulation_state=(
                mock_of[ClientSimulationState](
                    intake_engine=IntakeEngine(strategy=_AcceptingIntakeStrategy()),
                )
                if simulation_runtime
                else None
            ),
            persistence=mock_of[PersistenceBackend](
                projects=mock_of[ProjectRepository](),
            ),
            clock=FakeClock(),
            event_stream_hub=None,
            interrupt_store=None,
            agent_workspace_root=workspace,
            has_cost_tracker=cost_tracker is not None,
            cost_tracker=cost_tracker,
            has_message_bus=False,
            has_coordination_metrics_store=coordination_metrics_store is not None,
            coordination_metrics_store=coordination_metrics_store,
            has_audit_log=False,
            has_memory_backend=False,
            has_performance_tracker=False,
            has_trust_service=False,
        ),
    )


class TestProviderPresentSwitch:
    async def test_no_provider_returns_no_provider_runtime(
        self,
        tmp_path: Path,
    ) -> None:
        app_state = mock_of[AppState](has_active_provider=False)
        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )
        assert isinstance(result, RuntimeServices)
        assert isinstance(
            result.worker_execution_service,
            NoProviderExecutionService,
        )
        assert result.coordinator is None
        assert result.work_pipeline is None

    async def test_empty_registry_returns_no_provider_runtime(
        self,
        tmp_path: Path,
    ) -> None:
        app_state = mock_of[AppState](
            has_active_provider=True,
            provider_registry=ProviderRegistry({}),
        )
        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )
        assert isinstance(
            result.worker_execution_service,
            NoProviderExecutionService,
        )
        assert result.coordinator is None

    async def test_provider_present_returns_runtime_triple(
        self,
        tmp_path: Path,
    ) -> None:
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        app_state = _provider_app_state(registry, tmp_path)

        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )

        assert isinstance(
            result.worker_execution_service,
            AgentEngineExecutionService,
        )
        assert isinstance(result.coordinator, MultiAgentCoordinator)
        # No intake runtime wired in the default helper, so the spine
        # is intentionally unconfigured (honest unavailability).
        assert result.work_pipeline is None

    async def test_worker_and_coordinator_share_one_engine(
        self,
        tmp_path: Path,
    ) -> None:
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        app_state = _provider_app_state(registry, tmp_path)

        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )

        worker = result.worker_execution_service
        coordinator = result.coordinator
        assert isinstance(worker, AgentEngineExecutionService)
        assert coordinator is not None
        # The coordinator's parallel executor must run sub-agents on the
        # exact same boot AgentEngine as the worker execute seam.
        assert coordinator._parallel_executor._engine is worker._engine

    async def test_scorer_config_resolve_failure_is_fail_open(
        self,
        tmp_path: Path,
    ) -> None:
        """A bridge-config resolve failure must not break the build.

        ``_resolve_routing_scorer_config`` fails open (returns ``None``)
        so the coordinator is still built; the factory falls back to
        ``task_assignment_config.min_score``.
        """
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        app_state = _provider_app_state(
            registry,
            tmp_path,
            bridge_config_error=RuntimeError("settings backend down"),
        )

        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )

        assert isinstance(
            result.worker_execution_service,
            AgentEngineExecutionService,
        )
        assert isinstance(result.coordinator, MultiAgentCoordinator)

    async def test_multiple_providers_warns_and_selects_first(
        self,
        tmp_path: Path,
    ) -> None:
        registry = ProviderRegistry.from_config(
            {
                "test-provider": ProviderConfig(driver="scripted"),
                "test-provider-2": ProviderConfig(driver="scripted"),
            }
        )
        app_state = _provider_app_state(registry, tmp_path)
        # Exercises the >1-provider branch (warning + first-provider
        # selection); it must still build a live runtime, not reject.
        result = await build_runtime_services(
            app_state,
            workspace_root=tmp_path,
        )
        assert isinstance(
            result.worker_execution_service,
            AgentEngineExecutionService,
        )
        assert isinstance(result.coordinator, MultiAgentCoordinator)

    async def test_builds_nonexistent_deep_workspace_path(
        self,
        tmp_path: Path,
    ) -> None:
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        deep = tmp_path / "missing" / "agent" / "workspace"
        app_state = _provider_app_state(registry, deep)
        assert not deep.exists()

        result = await build_runtime_services(
            app_state,
            workspace_root=deep,
        )

        assert isinstance(
            result.worker_execution_service,
            AgentEngineExecutionService,
        )
        assert isinstance(result.coordinator, MultiAgentCoordinator)
        assert deep.is_dir()


class TestCoordinationMetricsWiring:
    """The coordination-metrics collector is built and shared at boot."""

    @staticmethod
    def _registry() -> ProviderRegistry:
        return ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )

    async def test_no_collector_without_cost_tracker(
        self,
        tmp_path: Path,
    ) -> None:
        app_state = _provider_app_state(self._registry(), tmp_path)
        result = await build_runtime_services(app_state, workspace_root=tmp_path)

        worker = result.worker_execution_service
        coordinator = result.coordinator
        assert isinstance(worker, AgentEngineExecutionService)
        assert coordinator is not None
        assert worker._engine._coordination_metrics_collector is None
        assert coordinator._coordination_metrics_collector is None

    async def test_collector_built_and_shared_when_cost_tracker_present(
        self,
        tmp_path: Path,
    ) -> None:
        store = CoordinationMetricsStore()
        app_state = _provider_app_state(
            self._registry(),
            tmp_path,
            cost_tracker=mock_of[CostTracker](),
            coordination_metrics_store=store,
        )
        result = await build_runtime_services(app_state, workspace_root=tmp_path)

        worker = result.worker_execution_service
        coordinator = result.coordinator
        assert isinstance(worker, AgentEngineExecutionService)
        assert coordinator is not None
        collector = worker._engine._coordination_metrics_collector
        assert isinstance(collector, CoordinationMetricsCollector)
        # Same single instance threaded into the single-agent engine AND
        # the multi-agent coordinator (no divergent collectors).
        assert coordinator._coordination_metrics_collector is collector
        assert collector._metrics_store is store

    async def test_baseline_window_size_from_setting(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SYNTHORG_BUDGET_BASELINE_WINDOW_SIZE", "7")
        app_state = _provider_app_state(
            self._registry(),
            tmp_path,
            cost_tracker=mock_of[CostTracker](),
            coordination_metrics_store=CoordinationMetricsStore(),
        )
        result = await build_runtime_services(app_state, workspace_root=tmp_path)

        worker = result.worker_execution_service
        assert isinstance(worker, AgentEngineExecutionService)
        collector = worker._engine._coordination_metrics_collector
        assert isinstance(collector, CoordinationMetricsCollector)
        baseline_store = collector._baseline_store
        assert baseline_store is not None
        assert baseline_store._window_size == 7


class TestWorkPipelineWiring:
    """The work pipeline spine shares the boot worker / coordinator / scorer."""

    @staticmethod
    def _registry() -> ProviderRegistry:
        return ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )

    async def test_pipeline_built_when_intake_runtime_present(
        self,
        tmp_path: Path,
    ) -> None:
        app_state = _provider_app_state(
            self._registry(),
            tmp_path,
            simulation_runtime=True,
        )

        result = await build_runtime_services(app_state, workspace_root=tmp_path)

        pipeline = result.work_pipeline
        coordinator = result.coordinator
        worker = result.worker_execution_service
        assert isinstance(pipeline, DefaultWorkPipeline)
        assert coordinator is not None
        # The spine holds the very same coordinator + worker instances
        # the tuple exposes (no divergent runtime surfaces).
        assert pipeline._coordinator is coordinator
        assert pipeline._worker_execution_service is worker
        # Solo and team routing share ONE scorer instance.
        assert pipeline._scorer is coordinator._routing_service._scorer

    async def test_pipeline_absent_without_intake_runtime(
        self,
        tmp_path: Path,
    ) -> None:
        app_state = _provider_app_state(
            self._registry(),
            tmp_path,
            simulation_runtime=False,
        )
        result = await build_runtime_services(app_state, workspace_root=tmp_path)
        assert result.work_pipeline is None
