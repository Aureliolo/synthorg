"""Tests for build_coordinator factory."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.config.schema import TaskAssignmentConfig
from synthorg.engine.coordination.factory import (
    _NoProviderDecompositionStrategy,
    build_coordinator,
)
from synthorg.engine.coordination.section_config import (
    CoordinationSectionConfig,
)
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.errors import DecompositionError
from synthorg.engine.parallel import ParallelExecutor
from synthorg.engine.routing.service import TaskRoutingService

pytestmark = pytest.mark.timeout(30)


def _mock_engine() -> MagicMock:
    """Create a mock AgentEngine for the factory."""
    return MagicMock()


@pytest.mark.unit
class TestBuildCoordinator:
    """build_coordinator() produces a working MultiAgentCoordinator."""

    def test_returns_coordinator(self) -> None:
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(),
        )
        assert isinstance(coordinator, MultiAgentCoordinator)

    def test_with_provider_and_model(self) -> None:
        """Provider and model are wired into decomposition strategy."""
        provider = AsyncMock()
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(),
            provider=provider,
            decomposition_model="test-model-001",
        )
        assert isinstance(coordinator, MultiAgentCoordinator)
        # Verify LLM strategy is used (not the placeholder)
        decomp = coordinator._decomposition_service
        assert isinstance(decomp, DecompositionService)
        assert not isinstance(decomp._strategy, _NoProviderDecompositionStrategy)

    def test_without_provider_uses_placeholder(self) -> None:
        """No provider/model → placeholder strategy."""
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(),
        )
        decomp = coordinator._decomposition_service
        assert isinstance(decomp._strategy, _NoProviderDecompositionStrategy)

    def test_with_task_engine(self) -> None:
        """task_engine is wired into the coordinator."""
        task_engine = AsyncMock()
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(),
            task_engine=task_engine,
        )
        assert coordinator._task_engine is task_engine

    def test_with_workspace_deps(self) -> None:
        """workspace_strategy + workspace_config produce a workspace service."""
        from synthorg.engine.workspace.config import (
            WorkspaceIsolationConfig,
        )
        from synthorg.engine.workspace.service import (
            WorkspaceIsolationService,
        )

        ws_strategy = MagicMock()
        ws_config = WorkspaceIsolationConfig()
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(),
            workspace_strategy=ws_strategy,
            workspace_config=ws_config,
        )
        ws_service = coordinator._workspace_service
        assert isinstance(ws_service, WorkspaceIsolationService)

    def test_custom_min_score(self) -> None:
        """min_score from TaskAssignmentConfig is forwarded to the scorer."""
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(min_score=0.5),
        )
        routing = coordinator._routing_service
        assert isinstance(routing, TaskRoutingService)
        assert routing._scorer._min_score == 0.5

    def test_shutdown_manager_passed_to_executor(self) -> None:
        """shutdown_manager is forwarded to the parallel executor."""
        shutdown_mgr = MagicMock()
        engine = _mock_engine()
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=engine,
            task_assignment_config=TaskAssignmentConfig(),
            shutdown_manager=shutdown_mgr,
        )
        executor = coordinator._parallel_executor
        assert isinstance(executor, ParallelExecutor)
        assert executor._shutdown_manager is shutdown_mgr
        assert executor._engine is engine

    def test_provider_only_raises_value_error(self) -> None:
        """Provider without model raises ValueError."""
        with pytest.raises(ValueError, match="missing decomposition_model"):
            build_coordinator(
                config=CoordinationSectionConfig(),
                engine=_mock_engine(),
                task_assignment_config=TaskAssignmentConfig(),
                provider=AsyncMock(),
            )

    def test_model_only_raises_value_error(self) -> None:
        """Model without provider raises ValueError."""
        with pytest.raises(ValueError, match="missing provider"):
            build_coordinator(
                config=CoordinationSectionConfig(),
                engine=_mock_engine(),
                task_assignment_config=TaskAssignmentConfig(),
                decomposition_model="test-model-001",
            )

    def test_workspace_strategy_only_raises_value_error(self) -> None:
        """workspace_strategy without workspace_config raises ValueError."""
        with pytest.raises(ValueError, match="missing workspace_config"):
            build_coordinator(
                config=CoordinationSectionConfig(),
                engine=_mock_engine(),
                task_assignment_config=TaskAssignmentConfig(),
                workspace_strategy=MagicMock(),
            )

    def test_workspace_config_only_raises_value_error(self) -> None:
        """workspace_config without workspace_strategy raises ValueError."""
        from synthorg.engine.workspace.config import (
            WorkspaceIsolationConfig,
        )

        with pytest.raises(ValueError, match="missing workspace_strategy"):
            build_coordinator(
                config=CoordinationSectionConfig(),
                engine=_mock_engine(),
                task_assignment_config=TaskAssignmentConfig(),
                workspace_config=WorkspaceIsolationConfig(),
            )


@pytest.mark.unit
class TestNoProviderDecompositionStrategy:
    """Placeholder strategy raises clear error."""

    async def test_raises_decomposition_error(self) -> None:
        strategy = _NoProviderDecompositionStrategy()
        with pytest.raises(
            DecompositionError,
            match="No LLM provider configured",
        ):
            await strategy.decompose(MagicMock(), MagicMock())
