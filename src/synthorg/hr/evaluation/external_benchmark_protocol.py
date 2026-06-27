"""External benchmark protocol for pluggable benchmark adoption.

Defines the ``ExternalBenchmark`` runtime-checkable protocol that
benchmark adapters implement, and the ``AgentRunner`` protocol that
produces the output a benchmark grades. No specific benchmark suites are
bundled; adopt one by registering an ``ExternalBenchmark`` adapter.
"""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from synthorg.execution.turn import BehaviorTag
from synthorg.hr.evaluation.external_benchmark_models import (
    BenchmarkGrade,
    EvalTestCase,
)


@runtime_checkable
class AgentRunner(Protocol):
    """Executes the agent under evaluation against a single test case.

    The returned string is the agent's raw output, the value
    :meth:`ExternalBenchmark.grade` scores. Injected into
    :class:`ExternalBenchmarkRegistry` so a benchmark run drives a live
    agent instead of self-grading the expected output.
    """

    async def run_case(self, case: EvalTestCase) -> str:
        """Run the agent for *case* and return its raw output string.

        Args:
            case: The test case to present to the agent.

        Returns:
            The agent's raw output string.
        """
        ...


@runtime_checkable
class ExternalBenchmark(Protocol):
    """Pluggable external benchmark adapter.

    Implementations provide test cases and grading logic for a
    specific benchmark suite.
    """

    @property
    def name(self) -> str:
        """Benchmark name (e.g. 'HumanEval', 'MATH-10K')."""
        ...

    @property
    def source_url(self) -> str:
        """URL to benchmark source or documentation."""
        ...

    @property
    def license(self) -> str:
        """License identifier (e.g. 'MIT', 'CC-BY-4.0')."""
        ...

    # Declared ``def`` (not ``async def``) on purpose: an async-generator
    # implementation's call returns an ``AsyncIterator`` directly with no
    # ``await``, so the protocol member is a plain callable returning
    # ``AsyncIterator``. ``async def`` here would type the call as a
    # coroutine and force every ``async for`` caller to ``await`` it first.
    def load_test_cases(
        self,
        *,
        behavior_tags: frozenset[BehaviorTag] | None = None,
    ) -> AsyncIterator[EvalTestCase]:
        """Stream test cases, optionally filtered by behavior tags.

        Implementations should be async generators (``async def``
        with ``yield``).

        Args:
            behavior_tags: Filter to these tags (``None`` = all).

        Yields:
            Test cases one at a time.
        """
        ...

    async def grade(
        self,
        *,
        case: EvalTestCase,
        agent_output: str,
    ) -> BenchmarkGrade:
        """Grade an agent's output for a test case.

        Args:
            case: The test case.
            agent_output: Agent's raw output string.

        Returns:
            Grade with pass/fail and explanation.
        """
        ...
