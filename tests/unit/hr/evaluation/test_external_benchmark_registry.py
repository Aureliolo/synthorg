"""Tests for ExternalBenchmarkRegistry."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from synthorg.execution.turn import BehaviorTag
from synthorg.hr.evaluation.errors import EvalBenchmarkAgentRunnerUnsetError
from synthorg.hr.evaluation.external_benchmark_models import (
    BenchmarkGrade,
    EvalTestCase,
)
from synthorg.hr.evaluation.external_benchmark_registry import (
    ExternalBenchmarkRegistry,
)


class _StubAgentRunner:
    """Configurable AgentRunner for exercising run_benchmark."""

    def __init__(
        self,
        *,
        echo_expected: bool = True,
        fixed_output: str | None = None,
        raise_on: str | None = None,
        raise_exc: type[BaseException] = RuntimeError,
    ) -> None:
        self._echo_expected = echo_expected
        self._fixed_output = fixed_output
        self._raise_on = raise_on
        self._raise_exc = raise_exc
        self.calls: list[str] = []

    async def run_case(self, case: EvalTestCase) -> str:
        self.calls.append(case.id)
        if self._raise_on is not None and case.id == self._raise_on:
            exc = self._raise_exc("boom")
            raise exc
        if self._fixed_output is not None:
            return self._fixed_output
        return case.expected_output if self._echo_expected else "WRONG"


class _StubBenchmark:
    """Minimal ExternalBenchmark implementation for testing."""

    def __init__(self, name: str = "test-bench") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def source_url(self) -> str:
        return "https://example.com"

    @property
    def license(self) -> str:
        return "MIT"

    async def load_test_cases(
        self,
        *,
        behavior_tags: frozenset[BehaviorTag] | None = None,
    ) -> AsyncIterator[EvalTestCase]:
        cases = [
            EvalTestCase(
                id="case-1",
                behavior_tags=(BehaviorTag.FILE_OPERATIONS,),
                input_data="test input 1",
                expected_output="expected 1",
            ),
            EvalTestCase(
                id="case-2",
                behavior_tags=(BehaviorTag.RETRIEVAL,),
                input_data="test input 2",
                expected_output="expected 2",
            ),
        ]
        for case in cases:
            if behavior_tags is None or (set(case.behavior_tags) & behavior_tags):
                yield case

    async def grade(
        self,
        *,
        case: EvalTestCase,
        agent_output: str,
    ) -> BenchmarkGrade:
        passed = agent_output == case.expected_output
        return BenchmarkGrade(
            passed=passed,
            score=1.0 if passed else 0.0,
            explanation="exact match" if passed else "mismatch",
        )


@pytest.mark.unit
class TestExternalBenchmarkRegistryRegistration:
    """Registration and lookup."""

    def test_register_and_get(self) -> None:
        registry = ExternalBenchmarkRegistry()
        bench = _StubBenchmark()
        registry.register(bench)
        assert registry.get("test-bench") is bench

    def test_get_missing_raises_key_error(self) -> None:
        registry = ExternalBenchmarkRegistry()
        with pytest.raises(KeyError, match="not registered"):
            registry.get("nonexistent")

    def test_duplicate_same_instance_ok(self) -> None:
        registry = ExternalBenchmarkRegistry()
        bench = _StubBenchmark()
        registry.register(bench)
        registry.register(bench)
        assert registry.get("test-bench") is bench

    def test_duplicate_different_instance_raises(self) -> None:
        registry = ExternalBenchmarkRegistry()
        registry.register(_StubBenchmark())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_StubBenchmark())

    def test_list_registered(self) -> None:
        registry = ExternalBenchmarkRegistry()
        registry.register(_StubBenchmark("alpha"))
        registry.register(_StubBenchmark("beta"))
        assert registry.list_registered() == ("alpha", "beta")

    def test_list_empty(self) -> None:
        registry = ExternalBenchmarkRegistry()
        assert registry.list_registered() == ()


@pytest.mark.unit
class TestExternalBenchmarkRegistryRunBenchmark:
    """run_benchmark drives a live agent runner."""

    async def test_run_benchmark_invokes_runner_all_pass(self) -> None:
        runner = _StubAgentRunner(echo_expected=True)
        registry = ExternalBenchmarkRegistry(agent_runner=runner)
        registry.register(_StubBenchmark())
        result = await registry.run_benchmark("test-bench")
        assert result.benchmark_name == "test-bench"
        assert result.cases_run == 2
        assert result.passed_count == 2
        assert result.average_score == 1.0
        assert runner.calls == ["case-1", "case-2"]

    async def test_run_benchmark_grades_agent_output_not_expected(self) -> None:
        registry = ExternalBenchmarkRegistry(
            agent_runner=_StubAgentRunner(echo_expected=False),
        )
        registry.register(_StubBenchmark())
        result = await registry.run_benchmark("test-bench")
        assert result.cases_run == 2
        assert result.passed_count == 0
        assert result.average_score == 0.0

    async def test_run_benchmark_without_runner_fails_closed(self) -> None:
        registry = ExternalBenchmarkRegistry()
        registry.register(_StubBenchmark())
        with pytest.raises(EvalBenchmarkAgentRunnerUnsetError):
            await registry.run_benchmark("test-bench")

    async def test_run_benchmark_missing_raises(self) -> None:
        registry = ExternalBenchmarkRegistry(agent_runner=_StubAgentRunner())
        with pytest.raises(KeyError):
            await registry.run_benchmark("nonexistent")

    async def test_run_benchmark_isolates_case_failure(self) -> None:
        runner = _StubAgentRunner(echo_expected=True, raise_on="case-1")
        registry = ExternalBenchmarkRegistry(agent_runner=runner)
        registry.register(_StubBenchmark())
        result = await registry.run_benchmark("test-bench")
        assert result.cases_run == 2
        assert result.passed_count == 1
        assert result.average_score == 0.5
        assert runner.calls == ["case-1", "case-2"]

    async def test_run_benchmark_propagates_critical(self) -> None:
        registry = ExternalBenchmarkRegistry(
            agent_runner=_StubAgentRunner(raise_on="case-1", raise_exc=RecursionError),
        )
        registry.register(_StubBenchmark())
        with pytest.raises(RecursionError):
            await registry.run_benchmark("test-bench")

    async def test_run_benchmark_with_completed_at(self) -> None:
        registry = ExternalBenchmarkRegistry(agent_runner=_StubAgentRunner())
        registry.register(_StubBenchmark())
        before = datetime.now(UTC)
        result = await registry.run_benchmark("test-bench")
        assert result.completed_at >= before
