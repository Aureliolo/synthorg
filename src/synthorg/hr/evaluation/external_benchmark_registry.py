"""External benchmark registry.

Manages registration and lookup of ``ExternalBenchmark``
implementations.  Provides a centralized run method for
executing benchmarks within evaluation cycles.
"""

import copy
from datetime import UTC, datetime
from types import MappingProxyType

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.execution.turn import BehaviorTag
from synthorg.hr.evaluation.errors import EvalBenchmarkAgentRunnerUnsetError
from synthorg.hr.evaluation.external_benchmark_models import (
    BenchmarkGrade,
    BenchmarkRunResult,
    EvalTestCase,
)
from synthorg.hr.evaluation.external_benchmark_protocol import (
    AgentRunner,
    ExternalBenchmark,
)
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.eval_loop import (
    EVAL_LOOP_BENCHMARK_ALREADY_REGISTERED,
    EVAL_LOOP_BENCHMARK_CASE_FAILED,
    EVAL_LOOP_BENCHMARK_EXECUTED,
    EVAL_LOOP_BENCHMARK_FAILED,
    EVAL_LOOP_BENCHMARK_NOT_FOUND,
)

logger = get_logger(__name__)


class ExternalBenchmarkRegistry:
    """Registry for pluggable external benchmarks.

    Supports registration, lookup, and benchmark execution.
    Internal storage is read-only (``MappingProxyType``) and
    mutated via copy-on-write in ``register()``.
    """

    def __init__(self, *, agent_runner: AgentRunner | None = None) -> None:
        self._benchmarks: MappingProxyType[str, ExternalBenchmark] = MappingProxyType(
            {}
        )
        self._agent_runner = agent_runner

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
            logger.warning(
                EVAL_LOOP_BENCHMARK_ALREADY_REGISTERED,
                benchmark_name=benchmark.name,
                error_type=ValueError.__name__,
            )
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
            logger.warning(
                EVAL_LOOP_BENCHMARK_NOT_FOUND,
                benchmark_name=name,
                error_type=KeyError.__name__,
            )
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
        """Run a single benchmark against the configured agent runner.

        Each test case is run through the injected :class:`AgentRunner`
        and graded on the agent's live output. A case that fails its run
        or grading is isolated (scored as failed) so one bad case never
        aborts the run.

        Args:
            name: Registered benchmark name.
            behavior_tags: Filter test cases by behavior tags.

        Returns:
            Aggregated benchmark run result.

        Raises:
            KeyError: If the benchmark is not registered.
            EvalBenchmarkAgentRunnerUnsetError: If no agent runner was
                configured on the registry.
        """
        benchmark = self.get(name)
        runner = self._require_agent_runner(name)
        cases_run = 0
        passed_count = 0
        total_score = 0.0

        async for case in benchmark.load_test_cases(
            behavior_tags=behavior_tags,
        ):
            grade = await self._grade_case(benchmark, runner, name, case)
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

    def _require_agent_runner(self, name: str) -> AgentRunner:
        """Return the configured agent runner or fail closed.

        Args:
            name: Benchmark name, for the failure log context.

        Returns:
            The configured :class:`AgentRunner`.

        Raises:
            EvalBenchmarkAgentRunnerUnsetError: If none was configured.
        """
        if self._agent_runner is None:
            logger.warning(
                EVAL_LOOP_BENCHMARK_FAILED,
                benchmark_name=name,
                error_type=EvalBenchmarkAgentRunnerUnsetError.__name__,
            )
            raise EvalBenchmarkAgentRunnerUnsetError
        return self._agent_runner

    async def _grade_case(
        self,
        benchmark: ExternalBenchmark,
        runner: AgentRunner,
        name: str,
        case: EvalTestCase,
    ) -> BenchmarkGrade:
        """Run and grade a single case, isolating non-critical failures.

        Args:
            benchmark: The benchmark being run.
            runner: The agent runner producing the output.
            name: Benchmark name, for the failure log context.
            case: The test case to run and grade.

        Returns:
            The grade, or a failed grade if the run or grading raised.
        """
        try:
            agent_output = await runner.run_case(case)
            return await benchmark.grade(case=case, agent_output=agent_output)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger,
                EVAL_LOOP_BENCHMARK_CASE_FAILED,
                exc,
                benchmark_name=name,
                case_id=case.id,
            )
            return BenchmarkGrade(
                passed=False,
                score=0.0,
                explanation="agent run or grading error",
            )
