"""Dogfooding dataset builder.

Filters production execution traces from the ``PerformanceTracker``
by behavior tag and quality score, and emits typed ``EvalDataset``
payloads for agent evaluation testing.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_protocol import BehaviorTag
from synthorg.hr.evaluation.external_benchmark_models import (
    EvalDataset,
    EvalTestCase,
)
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.observability import get_logger

logger = get_logger(__name__)


class DogfoodingDatasetConfig(BaseModel):
    """Configuration for dogfooding dataset construction.

    Attributes:
        max_cases_per_tag: Limit test cases per behavior tag.
        min_trace_quality: Minimum quality score to include.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_cases_per_tag: int = Field(
        default=100,
        ge=1,
        description="Limit test cases per behavior tag",
    )
    min_trace_quality: float = Field(
        default=7.0,
        ge=0.0,
        le=10.0,
        description="Minimum quality score to include",
    )


class DogfoodingDatasetBuilder:
    """Builds evaluation datasets from production execution traces.

    Queries the ``PerformanceTracker`` for task metric records,
    filters by quality score, and converts to ``EvalTestCase``
    objects.

    Args:
        performance_tracker: Source of production task metrics.
        config: Dataset construction configuration.
    """

    def __init__(
        self,
        *,
        performance_tracker: PerformanceTracker,
        config: DogfoodingDatasetConfig | None = None,
    ) -> None:
        self._tracker = performance_tracker
        self._config = config or DogfoodingDatasetConfig()

    @property
    def config(self) -> DogfoodingDatasetConfig:
        """Return the dataset configuration."""
        return self._config

    async def build(
        self,
        *,
        behavior_tags: frozenset[BehaviorTag] | None = None,
        min_quality_score: float | None = None,
        dataset_name: str = "dogfooding",
    ) -> EvalDataset:
        """Build an eval dataset from production traces.

        Args:
            behavior_tags: Filter to these tags (``None`` = all).
            min_quality_score: Override minimum quality threshold.
            dataset_name: Name for the resulting dataset.

        Returns:
            Curated evaluation dataset.
        """
        threshold = (
            min_quality_score
            if min_quality_score is not None
            else self._config.min_trace_quality
        )

        all_records = self._tracker.get_task_metrics()

        # Filter by quality score.
        qualified = tuple(
            r
            for r in all_records
            if r.quality_score is not None and r.quality_score >= threshold
        )

        # Convert to EvalTestCase objects.
        cases: list[EvalTestCase] = []
        tag_counts: dict[BehaviorTag, int] = {}

        for record in qualified:
            # Infer a behavior tag from task type.
            tag = _task_type_to_behavior_tag(record.task_type.value)
            if behavior_tags is not None and tag not in behavior_tags:
                continue

            count = tag_counts.get(tag, 0)
            if count >= self._config.max_cases_per_tag:
                continue

            case = EvalTestCase(
                id=NotBlankStr(record.id),
                behavior_tags=(tag,),
                input_data=f"task_id={record.task_id} type={record.task_type.value}",
                expected_output=(
                    f"quality={record.quality_score:.1f} success={record.is_success}"
                ),
            )
            cases.append(case)
            tag_counts[tag] = count + 1

        # Collect all unique tags present in the dataset.
        all_tags = tuple(
            sorted(
                {tag for case in cases for tag in case.behavior_tags},
                key=lambda t: t.value,
            )
        )

        now = datetime.now(UTC)

        return EvalDataset(
            name=NotBlankStr(dataset_name),
            source="dogfooding",
            behavior_tags=all_tags,
            test_cases=tuple(cases),
            created_at=now,
            version=NotBlankStr(f"v{now:%Y%m%d}"),
        )


def _task_type_to_behavior_tag(task_type: str) -> BehaviorTag:
    """Map a task type string to a behavior tag.

    Coarse mapping for dogfooding dataset construction.

    Returns:
        Result of type ``BehaviorTag``.
    """
    mapping: dict[str, BehaviorTag] = {
        "development": BehaviorTag.FILE_OPERATIONS,
        "research": BehaviorTag.RETRIEVAL,
        "review": BehaviorTag.VERIFICATION,
        "design": BehaviorTag.TOOL_USE,
        "meeting": BehaviorTag.CONVERSATION,
        "admin": BehaviorTag.COORDINATION,
    }
    return mapping.get(task_type, BehaviorTag.TOOL_USE)
