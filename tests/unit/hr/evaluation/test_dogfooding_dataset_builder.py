"""Tests for DogfoodingDatasetBuilder."""

from unittest.mock import MagicMock

import pytest

from synthorg.execution.turn import BehaviorTag
from synthorg.hr.evaluation.dogfooding_dataset_builder import (
    DogfoodingDatasetBuilder,
    DogfoodingDatasetConfig,
    _task_type_to_behavior_tag,
)


def _make_record(  # noqa: PLR0913
    *,
    agent_id: str = "agent-1",
    task_id: str = "task-1",
    record_id: str = "rec-1",
    task_type_value: str = "development",
    quality_score: float | None = 8.0,
    is_success: bool = True,
) -> MagicMock:
    """Build a mock TaskMetricRecord."""
    record = MagicMock()
    record.id = record_id
    record.agent_id = agent_id
    record.task_id = task_id
    record.task_type.value = task_type_value
    record.quality_score = quality_score
    record.is_success = is_success
    return record


def _make_tracker(*records: MagicMock) -> MagicMock:
    """Build a mock PerformanceTracker."""
    tracker = MagicMock()
    tracker.get_task_metrics.return_value = records
    return tracker


@pytest.mark.unit
class TestDogfoodingDatasetConfig:
    """DogfoodingDatasetConfig validation."""

    def test_defaults(self) -> None:
        config = DogfoodingDatasetConfig()
        assert config.max_cases_per_tag == 100
        assert config.min_trace_quality == 7.0


@pytest.mark.unit
class TestTaskTypeToBehaviorTag:
    """_task_type_to_behavior_tag mapping."""

    @pytest.mark.parametrize(
        ("task_type", "expected"),
        [
            ("development", BehaviorTag.FILE_OPERATIONS),
            ("research", BehaviorTag.RETRIEVAL),
            ("review", BehaviorTag.VERIFICATION),
            ("design", BehaviorTag.TOOL_USE),
            ("meeting", BehaviorTag.CONVERSATION),
            ("admin", BehaviorTag.COORDINATION),
            ("unknown", BehaviorTag.TOOL_USE),
        ],
    )
    def test_mapping(self, task_type: str, expected: BehaviorTag) -> None:
        assert _task_type_to_behavior_tag(task_type) == expected


@pytest.mark.unit
class TestDogfoodingDatasetBuilderBuild:
    """DogfoodingDatasetBuilder.build()."""

    async def test_build_empty_tracker(self) -> None:
        tracker = _make_tracker()
        builder = DogfoodingDatasetBuilder(performance_tracker=tracker)
        dataset = await builder.build()
        assert dataset.source == "dogfooding"
        assert dataset.test_cases == ()

    async def test_build_filters_by_quality(self) -> None:
        records = (
            _make_record(record_id="r1", quality_score=9.0),
            _make_record(record_id="r2", quality_score=3.0),
            _make_record(record_id="r3", quality_score=None),
        )
        tracker = _make_tracker(*records)
        builder = DogfoodingDatasetBuilder(performance_tracker=tracker)
        dataset = await builder.build()
        assert len(dataset.test_cases) == 1
        assert dataset.test_cases[0].id == "r1"

    async def test_build_with_quality_override(self) -> None:
        records = (
            _make_record(record_id="r1", quality_score=5.0),
            _make_record(record_id="r2", quality_score=3.0),
        )
        tracker = _make_tracker(*records)
        builder = DogfoodingDatasetBuilder(performance_tracker=tracker)
        dataset = await builder.build(min_quality_score=4.0)
        assert len(dataset.test_cases) == 1

    async def test_build_caps_per_tag(self) -> None:
        records = tuple(
            _make_record(record_id=f"r{i}", quality_score=9.0) for i in range(10)
        )
        tracker = _make_tracker(*records)
        config = DogfoodingDatasetConfig(max_cases_per_tag=3)
        builder = DogfoodingDatasetBuilder(
            performance_tracker=tracker,
            config=config,
        )
        dataset = await builder.build()
        assert len(dataset.test_cases) == 3

    async def test_build_filters_by_behavior_tags(self) -> None:
        records = (
            _make_record(
                record_id="r1",
                task_type_value="development",
                quality_score=9.0,
            ),
            _make_record(
                record_id="r2",
                task_type_value="research",
                quality_score=9.0,
            ),
        )
        tracker = _make_tracker(*records)
        builder = DogfoodingDatasetBuilder(performance_tracker=tracker)
        dataset = await builder.build(
            behavior_tags=frozenset({BehaviorTag.FILE_OPERATIONS}),
        )
        assert len(dataset.test_cases) == 1
        assert BehaviorTag.FILE_OPERATIONS in dataset.test_cases[0].behavior_tags

    async def test_build_dataset_name(self) -> None:
        tracker = _make_tracker()
        builder = DogfoodingDatasetBuilder(performance_tracker=tracker)
        dataset = await builder.build(dataset_name="custom-name")
        assert dataset.name == "custom-name"

    async def test_build_dataset_source(self) -> None:
        tracker = _make_tracker()
        builder = DogfoodingDatasetBuilder(performance_tracker=tracker)
        dataset = await builder.build()
        assert dataset.source == "dogfooding"
