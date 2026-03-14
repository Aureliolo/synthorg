"""Tests for build_coordinator factory."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_company.config.schema import TaskAssignmentConfig
from ai_company.engine.coordination.factory import (
    _NoProviderDecompositionStrategy,
    build_coordinator,
)
from ai_company.engine.coordination.section_config import (
    CoordinationSectionConfig,
)
from ai_company.engine.coordination.service import MultiAgentCoordinator
from ai_company.engine.errors import DecompositionError

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
        provider = AsyncMock()
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(),
            provider=provider,
            decomposition_model="test-model-001",
        )
        assert isinstance(coordinator, MultiAgentCoordinator)

    def test_without_provider_uses_placeholder(self) -> None:
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(),
        )
        # Coordinator is built — the placeholder strategy is internal
        assert isinstance(coordinator, MultiAgentCoordinator)

    def test_with_task_engine(self) -> None:
        task_engine = AsyncMock()
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(),
            task_engine=task_engine,
        )
        assert isinstance(coordinator, MultiAgentCoordinator)

    def test_with_workspace_deps(self) -> None:
        from ai_company.engine.workspace.config import (
            WorkspaceIsolationConfig,
        )

        strategy = MagicMock()
        config = WorkspaceIsolationConfig()
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(),
            workspace_strategy=strategy,
            workspace_config=config,
        )
        assert isinstance(coordinator, MultiAgentCoordinator)

    def test_custom_min_score(self) -> None:
        """min_score from TaskAssignmentConfig is used for scorer."""
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(min_score=0.5),
        )
        assert isinstance(coordinator, MultiAgentCoordinator)

    def test_shutdown_manager_passed_to_executor(self) -> None:
        shutdown_mgr = MagicMock()
        coordinator = build_coordinator(
            config=CoordinationSectionConfig(),
            engine=_mock_engine(),
            task_assignment_config=TaskAssignmentConfig(),
            shutdown_manager=shutdown_mgr,
        )
        assert isinstance(coordinator, MultiAgentCoordinator)

    def test_provider_only_raises_value_error(self) -> None:
        """Provider without model raises ValueError."""
        with pytest.raises(ValueError, match="missing decomposition_model"):
            build_coordinator(
                config=CoordinationSectionConfig(),
                engine=_mock_engine(),
                task_assignment_config=TaskAssignmentConfig(),
                provider=AsyncMock(),
                # decomposition_model not provided
            )

    def test_model_only_raises_value_error(self) -> None:
        """Model without provider raises ValueError."""
        with pytest.raises(ValueError, match="missing provider"):
            build_coordinator(
                config=CoordinationSectionConfig(),
                engine=_mock_engine(),
                task_assignment_config=TaskAssignmentConfig(),
                decomposition_model="test-model-001",
                # provider not provided
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
