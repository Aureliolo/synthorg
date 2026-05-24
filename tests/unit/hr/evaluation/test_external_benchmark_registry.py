"""Tests for ExternalBenchmarkRegistry."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from synthorg.engine.loop_protocol import BehaviorTag
from synthorg.hr.evaluation.external_benchmark_models import (
    BenchmarkGrade,
    EvalTestCase,
)
from synthorg.hr.evaluation.external_benchmark_registry import (
    ExternalBenchmarkRegistry,
)


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
    """run_benchmark execution."""

    async def test_run_benchmark_all_pass(self) -> None:
        registry = ExternalBenchmarkRegistry()
        registry.register(_StubBenchmark())
        result = await registry.run_benchmark("test-bench")
        assert result.benchmark_name == "test-bench"
        assert result.cases_run == 2
        assert result.passed_count == 2
        assert result.average_score == 1.0

    async def test_run_benchmark_missing_raises(self) -> None:
        registry = ExternalBenchmarkRegistry()
        with pytest.raises(KeyError):
            await registry.run_benchmark("nonexistent")

    async def test_run_benchmark_with_completed_at(self) -> None:
        registry = ExternalBenchmarkRegistry()
        registry.register(_StubBenchmark())
        before = datetime.now(UTC)
        result = await registry.run_benchmark("test-bench")
        assert result.completed_at >= before
