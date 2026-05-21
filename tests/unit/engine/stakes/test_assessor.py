"""Unit tests for stakes assessment (enum ordering, config, heuristic)."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import (
    Complexity,
    Priority,
    Stakes,
    TaskType,
    compare_stakes,
)
from synthorg.core.registry.errors import StrategyFactoryNotFoundError
from synthorg.core.task import Task
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.stakes import (
    DefaultStakesAssessor,
    StakesAssessmentConfig,
    build_stakes_assessor,
)
from synthorg.engine.stakes.config import ComplexityStakesRule


def _subtask(
    *,
    title: str = "Subtask",
    description: str = "Do the thing",
    complexity: Complexity = Complexity.MEDIUM,
) -> SubtaskDefinition:
    return SubtaskDefinition(
        id="st-1",
        title=title,
        description=description,
        estimated_complexity=complexity,
    )


def _task(
    *,
    title: str = "Task",
    description: str = "Do the thing",
    complexity: Complexity = Complexity.MEDIUM,
    priority: Priority = Priority.MEDIUM,
) -> Task:
    return Task(
        id="task-1",
        title=title,
        description=description,
        type=TaskType.DEVELOPMENT,
        project="proj-1",
        created_by="creator",
        estimated_complexity=complexity,
        priority=priority,
    )


@pytest.mark.unit
class TestStakesOrdering:
    """``compare_stakes`` follows LOW < NORMAL < HIGH < CRITICAL."""

    def test_strict_ascending_order(self) -> None:
        assert compare_stakes(Stakes.LOW, Stakes.NORMAL) < 0
        assert compare_stakes(Stakes.NORMAL, Stakes.HIGH) < 0
        assert compare_stakes(Stakes.HIGH, Stakes.CRITICAL) < 0

    def test_equal_is_zero(self) -> None:
        assert compare_stakes(Stakes.HIGH, Stakes.HIGH) == 0

    def test_reverse_is_positive(self) -> None:
        assert compare_stakes(Stakes.CRITICAL, Stakes.LOW) > 0

    def test_field_defaults_to_normal(self) -> None:
        assert _task().stakes is Stakes.NORMAL
        assert _subtask().stakes is Stakes.NORMAL


@pytest.mark.unit
class TestStakesAssessmentConfig:
    """Config defaults cover every complexity and reject duplicates."""

    def test_default_rules_cover_all_complexities(self) -> None:
        cfg = StakesAssessmentConfig()
        covered = {r.complexity for r in cfg.complexity_rules}
        assert covered == set(Complexity)

    def test_duplicate_complexity_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StakesAssessmentConfig(
                complexity_rules=(
                    ComplexityStakesRule(
                        complexity=Complexity.SIMPLE, stakes=Stakes.LOW
                    ),
                    ComplexityStakesRule(
                        complexity=Complexity.SIMPLE, stakes=Stakes.HIGH
                    ),
                ),
            )

    def test_config_is_frozen(self) -> None:
        cfg = StakesAssessmentConfig()
        with pytest.raises(ValidationError):
            cfg.assessor = "other"  # type: ignore[misc]


@pytest.mark.unit
class TestDefaultStakesAssessor:
    """Heuristic mapping, keyword bumps, priority elevation, fail-safe."""

    @pytest.mark.parametrize(
        ("complexity", "expected"),
        [
            (Complexity.SIMPLE, Stakes.LOW),
            (Complexity.MEDIUM, Stakes.NORMAL),
            (Complexity.COMPLEX, Stakes.HIGH),
            (Complexity.EPIC, Stakes.HIGH),
        ],
    )
    def test_complexity_base_mapping(
        self,
        complexity: Complexity,
        expected: Stakes,
    ) -> None:
        assessor = DefaultStakesAssessor()
        assert assessor.assess_subtask(_subtask(complexity=complexity)) is expected

    def test_high_keyword_elevates_to_high(self) -> None:
        assessor = DefaultStakesAssessor()
        subtask = _subtask(
            description="Make an architecture decision for the gateway",
            complexity=Complexity.SIMPLE,
        )
        assert assessor.assess_subtask(subtask) is Stakes.HIGH

    def test_critical_keyword_pins_to_critical(self) -> None:
        assessor = DefaultStakesAssessor()
        subtask = _subtask(
            description="This is an irreversible production change",
            complexity=Complexity.SIMPLE,
        )
        assert assessor.assess_subtask(subtask) is Stakes.CRITICAL

    def test_keyword_match_is_case_insensitive(self) -> None:
        assessor = DefaultStakesAssessor()
        subtask = _subtask(
            description="Touches the SECURITY boundary",
            complexity=Complexity.SIMPLE,
        )
        assert assessor.assess_subtask(subtask) is Stakes.HIGH

    def test_never_downgrades_below_complexity_base(self) -> None:
        """A complex subtask with no keyword stays HIGH, not pulled to NORMAL."""
        assessor = DefaultStakesAssessor()
        assert assessor.assess_subtask(_subtask(complexity=Complexity.COMPLEX)) is (
            Stakes.HIGH
        )

    def test_critical_priority_elevates_task(self) -> None:
        assessor = DefaultStakesAssessor()
        task = _task(complexity=Complexity.SIMPLE, priority=Priority.CRITICAL)
        assert assessor.assess_task(task) is Stakes.HIGH

    def test_critical_priority_elevation_can_be_disabled(self) -> None:
        assessor = DefaultStakesAssessor(
            StakesAssessmentConfig(elevate_on_critical_priority=False),
        )
        task = _task(complexity=Complexity.SIMPLE, priority=Priority.CRITICAL)
        assert assessor.assess_task(task) is Stakes.LOW

    def test_subtask_priority_path_does_not_elevate(self) -> None:
        """Subtasks carry no priority; only complexity/keywords apply."""
        assessor = DefaultStakesAssessor()
        assert assessor.assess_subtask(_subtask(complexity=Complexity.SIMPLE)) is (
            Stakes.LOW
        )


@pytest.mark.unit
class TestBuildStakesAssessor:
    """Factory dispatch on the ``assessor`` discriminator."""

    def test_default_builds_heuristic(self) -> None:
        assessor = build_stakes_assessor()
        assert isinstance(assessor, DefaultStakesAssessor)

    def test_explicit_heuristic(self) -> None:
        assessor = build_stakes_assessor(
            StakesAssessmentConfig(assessor="heuristic"),
        )
        assert isinstance(assessor, DefaultStakesAssessor)

    def test_unknown_assessor_raises(self) -> None:
        with pytest.raises(StrategyFactoryNotFoundError):
            build_stakes_assessor(StakesAssessmentConfig(assessor="nope"))
