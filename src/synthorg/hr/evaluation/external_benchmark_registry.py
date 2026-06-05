"""External benchmark registry.

Manages registration and lookup of ``ExternalBenchmark``
implementations.  Provides a centralized run method for
executing benchmarks within evaluation cycles.
"""

import copy
from datetime import UTC, datetime
from types import MappingProxyType

from synthorg.core.types import NotBlankStr
from synthorg.execution.turn import BehaviorTag
from synthorg.hr.evaluation.external_benchmark_models import (
    BenchmarkGrade,
    BenchmarkRunResult,
)
from synthorg.hr.evaluation.external_benchmark_protocol import (
    ExternalBenchmark,
)
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.eval_loop import (
    EVAL_LOOP_BENCHMARK_EXECUTED,
)

logger = get_logger(__name__)


class ExternalBenchmarkRegistry:
    """Registry for pluggable external benchmarks.

    Supports registration, lookup, and benchmark execution.
    Internal storage is read-only (``MappingProxyType``) and
    mutated via copy-on-write in ``register()``.
    """

    def __init__(self) -> None:
        self._benchmarks: MappingProxyType[str, ExternalBenchmark] = MappingProxyType(
            {}
        )

    def register(self, benchmark: ExternalBenchmark) -> None:
        """Register a benchmark by name.

        Args:
            benchmark: Benchmark adapter to register.

        Raises:
            ValueError: If a different benchmark is already
                registered under the same name.
        """
        existing = self._benchmarks.get(benchmark.name)
        if existing is not None and existing is not benchmark:
            msg = (
                f"Benchmark {benchmark.name!r} already registered "
                f"with a different instance"
            )
            logger.warning(msg)
            raise ValueError(msg)
        updated = copy.deepcopy(dict(self._benchmarks))
        updated[benchmark.name] = benchmark
        self._benchmarks = MappingProxyType(updated)

    def get(self, name: str) -> ExternalBenchmark:
        """Retrieve a benchmark by name.

        Args:
            name: Registered benchmark name.

        Returns:
            Result of type ``ExternalBenchmark``.

        Raises:
            KeyError: If the benchmark is not registered.
        """
        if name not in self._benchmarks:
            msg = f"Benchmark {name!r} not registered"
            logger.warning(msg)
            raise KeyError(msg)
        return self._benchmarks[name]

    def list_registered(self) -> tuple[str, ...]:
        """List all registered benchmark names.

        Returns:
            Tuple of ``str``.
        """
        return tuple(sorted(self._benchmarks))

    async def run_benchmark(
        self,
        name: str,
        *,
        behavior_tags: frozenset[BehaviorTag] | None = None,
    ) -> BenchmarkRunResult:
        """Run a single benchmark and collect results.

        Grades each test case against its expected output as a
        baseline self-check.  A real agent execution callback will
        be added when the eval loop wires up live agent runs.

        Args:
            name: Registered benchmark name.
            behavior_tags: Filter test cases by behavior tags.

        Returns:
            Aggregated benchmark run result.

        Raises:
            Exception: Raised when the relevant invariant fails.
        """
        benchmark = self.get(name)
        cases_run = 0
        passed_count = 0
        total_score = 0.0

        async for case in benchmark.load_test_cases(
            behavior_tags=behavior_tags,
        ):
            # In a real run, agent_output_fn would execute the agent.
            # For now, grade against the expected output as a baseline.
            try:
                grade: BenchmarkGrade = await benchmark.grade(
                    case=case,
                    agent_output=case.expected_output,
                )
            except Exception as exc:
                log_exception_redacted(
                    logger,
                    EVAL_LOOP_BENCHMARK_EXECUTED,
                    exc,
                    benchmark_name=name,
                    case_id=getattr(case, "id", "unknown"),
                    context="grading_error",
                )
                raise
            cases_run += 1
            if grade.passed:
                passed_count += 1
            total_score += grade.score

        avg_score = total_score / cases_run if cases_run > 0 else 0.0

        logger.info(
            EVAL_LOOP_BENCHMARK_EXECUTED,
            benchmark_name=name,
            cases_run=cases_run,
            passed_count=passed_count,
            average_score=avg_score,
        )

        return BenchmarkRunResult(
            benchmark_name=NotBlankStr(name),
            cases_run=cases_run,
            passed_count=passed_count,
            average_score=avg_score,
            completed_at=datetime.now(UTC),
        )
