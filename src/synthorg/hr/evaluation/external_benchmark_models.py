"""External benchmark and eval dataset models.

Frozen Pydantic models for evaluation test cases, benchmark grading,
benchmark run results, eval datasets, benchmark references, and
evaluation cycle reports.
"""

import copy
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.loop_protocol import BehaviorTag  # noqa: TC001
from synthorg.hr.evaluation.models import EvaluationReport  # noqa: TC001


class EvalTestCase(BaseModel):
    """Single test case from an external or dogfooding benchmark.

    Attributes:
        id: Unique case identifier.
        behavior_tags: Which behaviors this case tests.
        input_data: Serialized input to present to agent.
        expected_output: Ground truth (for reference grading).
        metadata: Additional benchmark-specific metadata.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique case identifier")
    behavior_tags: tuple[BehaviorTag, ...] = Field(
        description="Which behaviors this case tests",
    )
    input_data: str = Field(
        max_length=65536,
        description="Serialized input to present to agent",
    )
    expected_output: str = Field(
        max_length=65536,
        description="Ground truth for reference grading",
    )
    metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Additional benchmark-specific metadata",
    )

    def __init__(self, **data: object) -> None:
        """Deep-copy metadata dict at construction boundary."""
        if "metadata" in data and isinstance(data["metadata"], dict):
            data["metadata"] = copy.deepcopy(data["metadata"])
        super().__init__(**data)


class BenchmarkGrade(BaseModel):
    """Grading result for one test case.

    Attributes:
        passed: Whether the test passed.
        score: Numeric score (0.0-1.0).
        explanation: Human-readable grading rationale.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    passed: bool = Field(description="Whether the test passed")
    score: float = Field(
        ge=0.0,
        le=1.0,
        default=0.0,
        description="Numeric score (0.0-1.0)",
    )
    explanation: str = Field(
        max_length=2048,
        description="Human-readable grading rationale",
    )


class BenchmarkRunResult(BaseModel):
    """Results from running one benchmark in a cycle.

    Attributes:
        benchmark_name: Which benchmark was run.
        cases_run: Count of test cases executed.
        passed_count: Count of passing tests.
        average_score: Mean numeric score.
        completed_at: When the run finished.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    benchmark_name: NotBlankStr = Field(
        description="Which benchmark was run",
    )
    cases_run: int = Field(ge=0, description="Test cases executed")
    passed_count: int = Field(ge=0, description="Passing tests")
    average_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Mean numeric score",
    )
    completed_at: AwareDatetime = Field(
        description="When the run finished",
    )

    @model_validator(mode="after")
    def _validate_passed_within_total(self) -> Self:
        """Ensure passed_count does not exceed cases_run.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.passed_count > self.cases_run:
            msg = (
                f"passed_count ({self.passed_count}) cannot exceed "
                f"cases_run ({self.cases_run})"
            )
            raise ValueError(msg)
        return self


class EvalDataset(BaseModel):
    """Curated evaluation dataset.

    Attributes:
        name: Dataset identifier.
        source: Origin of the dataset.
        behavior_tags: Which behaviors this dataset covers.
        test_cases: Individual test cases.
        created_at: When dataset was built.
        version: Version identifier.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Dataset identifier")
    source: Literal["dogfooding", "external", "hand_written"] = Field(
        description="Origin of the dataset",
    )
    behavior_tags: tuple[BehaviorTag, ...] = Field(
        description="Which behaviors this dataset covers",
    )
    test_cases: tuple[EvalTestCase, ...] = Field(
        description="Individual test cases",
    )
    created_at: AwareDatetime = Field(
        description="When dataset was built",
    )
    version: NotBlankStr = Field(description="Version identifier")


class BenchmarkRef(BaseModel):
    """Reference to a registered external benchmark.

    Attributes:
        name: Benchmark name matching the registry key.
        enabled: Whether this benchmark is active.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(
        description="Benchmark name matching the registry key",
    )
    enabled: bool = Field(
        default=True,
        description="Whether this benchmark is active",
    )


class EvalCycleReport(BaseModel):
    """Complete results from one evaluation cycle.

    Attributes:
        cycle_id: Unique cycle identifier.
        window_start: Start of the evaluation window.
        window_end: End of the evaluation window.
        duration_seconds: Cycle execution time.
        agents_evaluated: Count of agents scored.
        agent_reports: Per-agent evaluation reports.
        observations: Identified failure patterns (stub: empty).
        proposed_actions: Recommended interventions (stub: empty).
        training_triggered: Whether training was queued.
        benchmark_results: External benchmark scores.
        created_at: When cycle completed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cycle_id: NotBlankStr = Field(description="Unique cycle identifier")
    window_start: AwareDatetime = Field(
        description="Start of the evaluation window",
    )
    window_end: AwareDatetime = Field(
        description="End of the evaluation window",
    )
    duration_seconds: float = Field(
        ge=0.0,
        description="Cycle execution time in seconds",
    )
    agents_evaluated: int = Field(
        ge=0,
        description="Count of agents scored",
    )
    agent_reports: tuple[EvaluationReport, ...] = Field(
        default=(),
        description="Per-agent evaluation reports",
    )
    observations: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Identified failure patterns (stub: empty)",
    )
    proposed_actions: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Recommended interventions (stub: empty)",
    )
    training_triggered: bool = Field(
        default=False,
        description="Whether training was queued",
    )
    benchmark_results: tuple[BenchmarkRunResult, ...] = Field(
        default=(),
        description="External benchmark scores",
    )
    created_at: AwareDatetime = Field(
        description="When cycle completed",
    )

    @model_validator(mode="after")
    def _validate_window_order(self) -> Self:
        """Ensure window_start is not after window_end.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.window_start > self.window_end:
            msg = (
                f"window_start ({self.window_start}) cannot be "
                f"after window_end ({self.window_end})"
            )
            raise ValueError(msg)
        return self
