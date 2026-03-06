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
    """Budget exhaustion signal for the engine layer.

    The execution loop returns ``TerminationReason.BUDGET_EXHAUSTED``
    internally.  This exception is available for the engine layer above
    the loop to convert that result into a raised error when appropriate.
    """


class LoopExecutionError(EngineError):
    """Non-recoverable execution loop error for the engine layer.

    The execution loop returns ``TerminationReason.ERROR`` internally.
    This exception is available for the engine layer above the loop to
    convert that result into a raised error when appropriate.
    """
