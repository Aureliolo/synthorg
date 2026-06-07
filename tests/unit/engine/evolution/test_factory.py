"""Tests for evolution factory assembly."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.engine.evolution.config import (
    EvolutionConfig,
    ProposerConfig,
    TriggerConfig,
)
from synthorg.engine.evolution.factory import build_evolution_service
from synthorg.engine.evolution.service import EvolutionService
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.versioning.service import VersioningService
from tests._shared import as_uuid


def _build_service(
    config: EvolutionConfig | None = None,
    *,
    provider: MagicMock | None = None,
) -> EvolutionService:
    """Build a service from config with mock dependencies."""
    if config is None:
        config = EvolutionConfig()
    repo = AsyncMock()
    return build_evolution_service(
        config,
        registry=AgentRegistryService(),
        versioning=VersioningService(repo),
        tracker=PerformanceTracker(),
        provider=provider,
    )


class TestBuildEvolutionService:
    """build_evolution_service creates a valid service."""

    @pytest.mark.unit
    def test_default_config_builds(self) -> None:
        service = _build_service()
        assert isinstance(service, EvolutionService)

    @pytest.mark.unit
    def test_disabled_config_builds(self) -> None:
        config = EvolutionConfig(enabled=False)
        service = _build_service(config)
        assert isinstance(service, EvolutionService)

    @pytest.mark.unit
    def test_per_task_trigger(self) -> None:
        config = EvolutionConfig(
            triggers=TriggerConfig(types=("per_task",)),
        )
        service = _build_service(config)
        assert isinstance(service, EvolutionService)

    @pytest.mark.unit
    def test_composite_proposer_without_provider(self) -> None:
        """Without a provider, falls back to self_report."""
        config = EvolutionConfig(
            proposer=ProposerConfig(type="composite"),
        )
        service = _build_service(config)
        assert isinstance(service, EvolutionService)

    @pytest.mark.unit
    def test_separate_analyzer_with_provider(self) -> None:
        config = EvolutionConfig(
            proposer=ProposerConfig(type="separate_analyzer"),
        )
        provider = MagicMock()
        service = _build_service(config, provider=provider)
        assert isinstance(service, EvolutionService)


class TestBuildShadowGuard:
    """Shadow-evaluation wiring errors surface structured logs + ValueError."""

    def _shadow_config(
        self,
        *,
        task_provider: str = "configured",
    ) -> EvolutionConfig:
        from synthorg.core.enums import TaskType
        from synthorg.core.task import AcceptanceCriterion, Task
        from synthorg.engine.evolution.config import (
            GuardConfig,
            ShadowEvaluationConfig,
        )

        probe_tasks: tuple[Task, ...] = ()
        if task_provider == "configured":
            probe_tasks = (
                Task(
                    id=as_uuid("probe-1"),
                    title="probe",
                    description="probe",
                    type=TaskType.DEVELOPMENT,
                    project="proj-shadow",
                    created_by="creator",
                    acceptance_criteria=(AcceptanceCriterion(description="c"),),
                ),
            )
        return EvolutionConfig(
            guards=GuardConfig(
                shadow_evaluation=ShadowEvaluationConfig(
                    task_provider=task_provider,  # type: ignore[arg-type]
                    probe_tasks=probe_tasks,
                ),
            ),
        )

    @pytest.mark.unit
    def test_missing_shadow_runner_raises(self) -> None:
        config = self._shadow_config()
        with pytest.raises(ValueError, match="shadow_runner"):
            _build_service(config)

    @pytest.mark.unit
    def test_recent_history_missing_sampler_raises(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        config = self._shadow_config(task_provider="recent_history")
        runner = _AsyncMock()
        repo = AsyncMock()
        with pytest.raises(ValueError, match="shadow_task_sampler"):
            build_evolution_service(
                config,
                registry=AgentRegistryService(),
                versioning=VersioningService(repo),
                tracker=PerformanceTracker(),
                shadow_runner=runner,
            )

    @pytest.mark.unit
    def test_shadow_wired_with_runner_and_sampler(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        from synthorg.engine.evolution.guards.composite import CompositeGuard
        from synthorg.engine.evolution.guards.shadow_evaluation import (
            ShadowEvaluationGuard,
        )
        from synthorg.engine.evolution.guards.shadow_providers import (
            RecentTaskHistoryProvider,
        )

        config = self._shadow_config(task_provider="recent_history")
        runner = _AsyncMock()
        sampler = _AsyncMock(return_value=())
        repo = AsyncMock()
        service = build_evolution_service(
            config,
            registry=AgentRegistryService(),
            versioning=VersioningService(repo),
            tracker=PerformanceTracker(),
            shadow_runner=runner,
            shadow_task_sampler=sampler,
        )
        assert isinstance(service, EvolutionService)

        # Dig into the guard chain to confirm the shadow guard is
        # actually wired with the supplied runner and a
        # ``RecentTaskHistoryProvider`` that references the sampler.
        guard = service._guard
        shadow_guards = (
            tuple(g for g in guard._guards if isinstance(g, ShadowEvaluationGuard))
            if isinstance(guard, CompositeGuard)
            else (guard,)
            if isinstance(guard, ShadowEvaluationGuard)
            else ()
        )
        assert len(shadow_guards) == 1
        shadow = shadow_guards[0]
        assert shadow._runner is runner
        assert isinstance(shadow._task_provider, RecentTaskHistoryProvider)
        assert shadow._task_provider._sampler is sampler
