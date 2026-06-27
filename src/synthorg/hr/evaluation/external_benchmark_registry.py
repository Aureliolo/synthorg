"""External benchmark registry.

Manages registration and lookup of ``ExternalBenchmark``
implementations. Provides a centralised run method for
executing benchmarks within evaluation cycles.
"""

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
    EVAL_LOOP_BENCHMARK_NOT_FOUND,
    EVAL_LOOP_BENCHMARK_STARTED,
)

logger = get_logger(__name__)


class ExternalBenchmarkRegistry:
    """Registry for pluggable external benchmarks.

    Supports registration, lookup, and benchmark execution.
    Internal storage is read-only (``MappingProxyType``) and
    mutated by rebuilding the mapping in ``register()``; benchmark
    instances are held by reference (identity is the registration key).
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
        if existing is not None:
            if existing is benchmark:
                return
            logger.warning(
                EVAL_LOOP_BENCHMARK_ALREADY_REGISTERED,
                benchmark_name=benchmark.name,
                error_type=ValueError.__name__,
            )
            msg = (
                f"Benchmark {benchmark.name!r} already registered "
                f"with a different instance"
            )
            raise ValueError(msg)
        updated = dict(self._benchmarks)
        updated[benchmark.name] = benchmark
        self._benchmarks = MappingProxyType(updated)

    def get(self, name: str) -> ExternalBenchmark:
        """Retrieve a benchmark by name.

        Args:
            name: Registered benchmark name.

        Returns:
            The registered :class:`ExternalBenchmark`.

        Raises:
            KeyError: If the benchmark is not registered.
        """
        if name not in self._benchmarks:
            logger.warning(
                EVAL_LOOP_BENCHMARK_NOT_FOUND,
                benchmark_name=name,
                error_type=KeyError.__name__,
            )
            msg = f"Benchmark {name!r} not registered"
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
        and graded on the agent's live output. A case that raises a
        non-critical exception is isolated (scored as failed) so one bad
        case never aborts the run.

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
        runner = self._require_agent_runner()
        logger.debug(EVAL_LOOP_BENCHMARK_STARTED, benchmark_name=name)
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

    def _require_agent_runner(self) -> AgentRunner:
        """Return the configured agent runner or fail closed.

        The fail-closed raise carries a self-describing typed error, so it
        is left unlogged here; the run's caller owns logging the failure
        (the registry must not double-log the benchmark's outcome).

        Returns:
            The configured :class:`AgentRunner`.

        Raises:
            EvalBenchmarkAgentRunnerUnsetError: If none was configured.
        """
        if self._agent_runner is None:
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

        The agent run and the grading are isolated separately so a failed
        grade records which stage broke (an infra/agent failure versus a
        bug in the benchmark adapter), which drive different fixes.

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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            return self._failed_grade(exc, name, case, stage="runner")
        try:
            return await benchmark.grade(case=case, agent_output=agent_output)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            return self._failed_grade(exc, name, case, stage="grader")

    def _failed_grade(
        self,
        exc: Exception,
        name: str,
        case: EvalTestCase,
        *,
        stage: str,
    ) -> BenchmarkGrade:
        """Log an isolated case failure and return a zero-score grade.

        Args:
            exc: The caught exception (re-raised if interpreter-critical).
            name: Benchmark name, for the failure log context.
            case: The failing test case.
            stage: Which stage raised (``"runner"`` or ``"grader"``).

        Returns:
            A failed :class:`BenchmarkGrade` carrying the exception type.

        Raises:
            BaseException: Re-raised when *exc* is interpreter-critical.
        """
        reraise_critical(exc)
        log_exception_redacted(
            logger,
            EVAL_LOOP_BENCHMARK_CASE_FAILED,
            exc,
            benchmark_name=name,
            case_id=case.id,
            stage=stage,
        )
        return BenchmarkGrade(
            passed=False,
            score=0.0,
            explanation=f"{stage} error: {type(exc).__name__}",
        )
