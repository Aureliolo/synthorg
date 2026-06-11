"""Unit tests for the provider-present runtime-services switch."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.budget.coordination_collector import CoordinationMetricsCollector
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker import CostTracker
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.provider_schema import ProviderConfig
from synthorg.config.schema import RootConfig
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.parallel import ParallelExecutor
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.protocol import CompletionProvider
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.bridge_configs import EngineBridgeConfig
from synthorg.settings.resolver import ConfigResolver
from synthorg.workers._coordinator_assembly import _build_runtime_coordinator
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    NoProviderExecutionService,
)
from synthorg.workers.runtime_builder import (
    RuntimeServices,
    build_runtime_services,
)
from tests._shared import FakeClock, make_app_state, mock_of

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
    decomposition_error: Exception | None = None,
    cost_tracker: CostTracker | None = None,
    coordination_metrics_store: CoordinationMetricsStore | None = None,
    simulation_runtime: bool = False,
) -> AppState:
    """Build a mocked AppState for the provider-present path.

    ``bridge_config_error`` makes ``get_engine_bridge_config`` raise, to
    exercise the fail-open routing-scorer-config resolve branch.
    ``decomposition_error`` makes ``get_str`` raise on the
    ``decomposition_model`` key, to exercise the
    ``_build_runtime_coordinator`` redacted-log + re-raise branch.
    ``cost_tracker`` (and the paired ``coordination_metrics_store``)
    drive the coordination-metrics collector wiring: absent, the
    collector is not constructed (mirrors the empty/degraded path).
    """
    if bridge_config_error is None:
        bridge_mock = AsyncMock(return_value=EngineBridgeConfig())
    else:
        bridge_mock = AsyncMock(side_effect=bridge_config_error)
    if decomposition_error is None:
        get_str_mock = AsyncMock(side_effect=_get_str)
    else:
        # Narrow-raise on the decomposition key only so the upstream
        # boot calls (browser settings, sandbox images, etc.) still
        # resolve normally; we want the failure to surface from the
        # `_build_runtime_coordinator` TaskGroup, not from
        # `_build_tool_registry` upstream of it.
        async def _get_str_failing(namespace: str, key: str) -> str:
            if key == "decomposition_model":
                raise decomposition_error
            return await _get_str(namespace, key)

        get_str_mock = AsyncMock(side_effect=_get_str_failing)
    return make_app_state(
        provider_registry=registry,
        config=RootConfig(company_name="test-corp"),
        config_resolver=mock_of[ConfigResolver](
            get_float=AsyncMock(return_value=30.0),
            get_str=get_str_mock,
            get_int=AsyncMock(return_value=1),
            get_engine_bridge_config=bridge_mock,
        ),
        task_engine=mock_of[TaskEngine](),
        agent_registry=AgentRegistryService(),
        # A real store: the engine builder's ``require_service`` needs a
        # wired approval store. The old ``mock_of[AppState]`` auto-filled
        # the slice read, masking that requirement with ``approval_store=None``.
        approval_store=ApprovalStore(),
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
        agent_workspace_root=workspace,
        cost_tracker=cost_tracker,
        coordination_metrics_store=coordination_metrics_store,
    )


class TestProviderPresentSwitch:
    async def test_no_provider_returns_no_provider_runtime(
        self,
        tmp_path: Path,
    ) -> None:
        app_state = make_app_state()
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
        app_state = make_app_state(provider_registry=ProviderRegistry({}))
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
        # exact same boot AgentEngine as the worker execute seam. The
        # coordinator holds it by the ``ParallelExecutorProtocol`` surface;
        # the boot build always wires the concrete ``ParallelExecutor``.
        parallel_executor = cast("ParallelExecutor", coordinator._parallel_executor)
        assert parallel_executor._engine is worker._engine

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


class TestBootLogSafetySpineState:
    """The boot log carries the safety-spine state on every branch.

    Operators reading ``synthorg.log`` must see whether the SecOps
    interceptor is ``active`` / ``shadow`` / ``disabled`` at startup
    without grepping config files; the agent runtime's go/no-go decision
    log is the single observable place for it.
    """

    @staticmethod
    def _runtime_services_logs(
        logs: Sequence[Mapping[str, object]],
    ) -> list[Mapping[str, object]]:
        return [
            entry
            for entry in logs
            if entry.get("event") == API_APP_STARTUP
            and entry.get("service") == "runtime_services"
        ]

    async def test_no_provider_log_carries_safety_spine_state(
        self,
        tmp_path: Path,
    ) -> None:
        """Empty-company boot still emits the safety-spine fields."""
        app_state = make_app_state(config=RootConfig(company_name="empty-co"))
        with capture_logs() as logs:
            await build_runtime_services(app_state, workspace_root=tmp_path)
        runtime_logs = self._runtime_services_logs(logs)
        assert runtime_logs, "no runtime_services boot log captured"
        no_provider = next(e for e in runtime_logs if e.get("mode") == "no_provider")
        assert no_provider["security_enabled"] is True
        assert no_provider["security_enforcement_mode"] == "active"

    async def test_empty_registry_log_carries_safety_spine_state(
        self,
        tmp_path: Path,
    ) -> None:
        """Provider-registry-empty boot still emits the safety-spine fields."""
        app_state = make_app_state(
            provider_registry=ProviderRegistry({}),
            config=RootConfig(company_name="registry-empty-co"),
        )
        with capture_logs() as logs:
            await build_runtime_services(app_state, workspace_root=tmp_path)
        runtime_logs = self._runtime_services_logs(logs)
        no_provider = next(e for e in runtime_logs if e.get("mode") == "no_provider")
        assert no_provider["security_enabled"] is True
        assert no_provider["security_enforcement_mode"] == "active"

    async def test_provider_present_log_carries_safety_spine_state(
        self,
        tmp_path: Path,
    ) -> None:
        """Provider-present boot emits the spine fields on both startup events.

        The ``agent_engine`` decision log AND the post-construction
        ``agent_engine_built`` summary log must both surface the spine
        state -- otherwise operators reading the boot trail see schema
        drift between the two ``runtime_services`` events.
        """
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        app_state = _provider_app_state(registry, tmp_path)
        with capture_logs() as logs:
            await build_runtime_services(app_state, workspace_root=tmp_path)
        runtime_logs = self._runtime_services_logs(logs)
        agent_engine = next(e for e in runtime_logs if e.get("mode") == "agent_engine")
        assert agent_engine["security_enabled"] is True
        assert agent_engine["security_enforcement_mode"] == "active"
        agent_engine_built = next(
            e for e in runtime_logs if e.get("mode") == "agent_engine_built"
        )
        assert agent_engine_built["security_enabled"] is True
        assert agent_engine_built["security_enforcement_mode"] == "active"


class TestRuntimeCoordinatorResolveFailure:
    """The coordinator resolve-failure path logs redacted context and re-raises.

    The ``_build_runtime_coordinator`` TaskGroup runs three independent
    config resolves (decomposition model, routing-scorer bridge,
    workspace strategy). When any of them raises an
    ``ExceptionGroup``-propagated error, the wrapper must record the
    failure with the SecOps-safe redactor and surface the exception to
    the boot caller so the API doesn't come up with a half-wired
    coordinator.
    """

    async def test_resolve_failure_logs_and_propagates(
        self,
        tmp_path: Path,
    ) -> None:
        """Decomposition resolve failure surfaces a redacted log + raises.

        Drives ``_build_runtime_coordinator`` directly rather than the full
        ``build_runtime_services`` entry point: the resolve-failure
        behaviour lives entirely in this TaskGroup wrapper. Bypassing the
        entry point means no tool registry and no boot ``AgentEngine`` are
        constructed at all (the failure path never reaches them anyway).
        ``engine`` and ``provider`` are only consumed AFTER the TaskGroup
        succeeds, so stub doubles suffice on the failure path.
        """
        registry = ProviderRegistry.from_config(
            {"test-provider": ProviderConfig(driver="scripted")}
        )
        app_state = _provider_app_state(
            registry,
            tmp_path,
            decomposition_error=RuntimeError("decomposition backend unreachable"),
        )
        with (
            capture_logs() as logs,
            pytest.raises(BaseExceptionGroup) as excinfo,
        ):
            await _build_runtime_coordinator(
                app_state,
                mock_of[AgentEngine](),
                mock_of[CompletionProvider](),
                None,
            )
        # TaskGroup collapses task failures into a BaseExceptionGroup
        # whose ``str()`` is the generic "unhandled errors in a TaskGroup"
        # banner -- the original RuntimeError must travel inside it
        # (asserted by manual unwrap, not by ``pytest.raises(match=...)``
        # which only matches the outer banner).
        flattened = list(excinfo.value.exceptions)
        assert any(
            isinstance(exc, RuntimeError)
            and "decomposition backend unreachable" in str(exc)
            for exc in flattened
        )
        coordinator_failure = next(
            (
                entry
                for entry in logs
                if entry.get("event") == API_APP_STARTUP
                and entry.get("service") == "coordinator"
                and entry.get("context") == "resolve_failed"
            ),
            None,
        )
        assert coordinator_failure is not None, (
            "coordinator resolve_failed log not emitted"
        )
        # The redactor surfaces the typed exception name and the
        # canonical ``{Type}: {scrubbed-message}`` shape, never the raw
        # traceback -- attaching ``exc_info`` would serialise the
        # frame-locals (incl. any in-scope credential) into the record.
        # ``_build_runtime_coordinator`` catches the TaskGroup's
        # ExceptionGroup wrapper, so the typed name on the log is
        # ``ExceptionGroup`` (the original ``RuntimeError`` is asserted
        # above on the propagated exception).
        assert coordinator_failure["error_type"] == "ExceptionGroup"
        assert coordinator_failure["error"].startswith("ExceptionGroup")
        assert coordinator_failure["note"].startswith(
            "decomposition / routing-scorer / workspace config resolve failed"
        )
        assert "exc_info" not in coordinator_failure


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
