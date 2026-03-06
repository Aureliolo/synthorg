"""Engine-layer error hierarchy."""


class EngineError(Exception):
    """Base exception for all engine-layer errors."""


class PromptBuildError(EngineError):
    """Raised when system prompt construction fails."""


class ExecutionStateError(EngineError):
    """Raised when an execution state transition is invalid."""


class MaxTurnsExceededError(EngineError):
    """Raised when ``turn_count`` reaches ``max_turns`` during execution.

    Enforced by ``AgentContext.with_turn_completed`` when the hard turn
    limit has been reached.
    """


class BudgetExhaustedError(EngineError):
    """Raised when the budget checker signals exhaustion before an LLM call."""


class LoopExecutionError(EngineError):
    """Raised when the execution loop encounters a non-recoverable error."""
