"""Engine-layer error hierarchy."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from synthorg.engine.coordination.models import CoordinationPhaseResult


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


class TaskEngineError(EngineError):
    """Base exception for all task engine errors."""


class TaskEngineNotRunningError(TaskEngineError):
    """Raised when a mutation is submitted to a stopped task engine."""


class TaskEngineQueueFullError(TaskEngineError):
    """Raised when the task engine queue is at capacity."""


class TaskMutationError(TaskEngineError):
    """Raised when a task mutation fails (not found, validation, etc.)."""


class TaskNotFoundError(TaskMutationError):
    """Raised when a task is not found during mutation."""


class TaskVersionConflictError(TaskMutationError):
    """Raised when optimistic concurrency version does not match."""


class TaskInternalError(TaskEngineError):
    """Raised when a task mutation fails due to an internal engine error.

    Unlike :class:`TaskMutationError` (which covers business-rule failures
    such as validation or not-found), this signals an unexpected engine fault
    that the caller cannot fix by changing the request. Maps to 5xx at the API
    layer.

    This is deliberately a sibling of ``TaskMutationError``, not a subtype,
    so that broad ``except TaskMutationError`` handlers do not accidentally
    catch internal engine faults.
    """


class CoordinationError(EngineError):
    """Base exception for multi-agent coordination failures."""


class CoordinationPhaseError(CoordinationError):
    """Raised when a coordination pipeline phase fails.

    Carries the failing phase name and all phase results accumulated
    up to and including the failure, enabling partial-result inspection.

    Attributes:
        phase: Name of the phase that failed.
        partial_phases: Phase results accumulated before and including
            this failure.
    """

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        partial_phases: tuple[CoordinationPhaseResult, ...] = (),
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.partial_phases = partial_phases
