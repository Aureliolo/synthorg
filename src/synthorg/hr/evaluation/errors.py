"""Domain errors for the HR evaluation / eval-loop subsystem."""

from typing import ClassVar

from synthorg.core.error_taxonomy import ErrorCode
from synthorg.hr.errors import HRError


class EvaluationError(HRError):
    """Base error for the HR evaluation / eval-loop subsystem."""


class EvalBenchmarkAgentRunnerUnsetError(EvaluationError):
    """A benchmark run was requested with no agent runner configured.

    Fail-closed: a benchmark grades the agent's live output, so running
    one without an injected :class:`AgentRunner` would either silently
    grade nothing or regress to self-grading the expected output. The
    registry refuses the run instead.
    """

    default_message: ClassVar[str] = (
        "Benchmark run requires an agent runner; none was configured"
    )
    error_code: ClassVar[ErrorCode] = ErrorCode.EVAL_BENCHMARK_RUNNER_UNSET
