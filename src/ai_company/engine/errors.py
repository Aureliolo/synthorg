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


class LoopExecutionError(EngineError):
    """Non-recoverable execution loop error for the engine layer.

    The execution loop returns ``TerminationReason.ERROR`` internally.
    This exception is available for the engine layer above the loop to
    convert that result into a raised error when appropriate.
    """


class ParallelExecutionError(EngineError):
    """Raised when a parallel execution group encounters a fatal error."""


class ResourceConflictError(EngineError):
    """Raised when resource claims conflict between assignments."""


class DecompositionError(EngineError):
    """Base exception for task decomposition failures."""


class DecompositionCycleError(DecompositionError):
    """Raised when a dependency cycle is detected in the subtask graph."""


class DecompositionDepthError(DecompositionError):
    """Raised when decomposition exceeds the maximum nesting depth."""


class TaskRoutingError(EngineError):
    """Raised when task routing to an agent fails."""


class TaskAssignmentError(EngineError):
    """Raised when task assignment fails."""


class NoEligibleAgentError(TaskAssignmentError):
    """Raised when no eligible agent is found for assignment."""


class WorkspaceError(EngineError):
    """Base exception for workspace isolation failures."""


class WorkspaceSetupError(WorkspaceError):
    """Raised when workspace creation fails."""


class WorkspaceMergeError(WorkspaceError):
    """Raised when workspace merge fails."""


class WorkspaceCleanupError(WorkspaceError):
    """Raised when workspace teardown fails."""


class WorkspaceLimitError(WorkspaceError):
    """Raised when maximum concurrent workspaces reached."""
