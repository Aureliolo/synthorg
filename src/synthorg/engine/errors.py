"""Engine-layer error hierarchy."""

from typing import TYPE_CHECKING, ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.engine.coordination.models import CoordinationPhaseResult


class EngineError(DomainError):
    """Base exception for all engine-layer errors.

    Inherits from :class:`DomainError` so the prefix-vs-category
    validator runs on every subclass (audit
    ``34-error-handling-consistency``); a typo in a subclass
    ``error_code`` whose first digit no longer matches the declared
    ``error_category`` is rejected at class-definition time.

    Class Attributes:
        status_code: Default HTTP status for API exposure (500).
        error_code: Default RFC 9457 error code.
        error_category: Default RFC 9457 error category.
        retryable: Whether the client should retry the request.
        default_message: Generic 5xx-safe message used by exception handlers.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.ENGINE_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Internal server error"


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


class ProjectNotFoundError(EngineError):
    """Referenced project does not exist.

    The message is deliberately generic to avoid leaking internal
    identifiers.  The ``project_id`` attribute is available for
    structured logs but must NOT be included in user-facing responses.

    Attributes:
        project_id: The project identifier that was not found.
    """

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.PROJECT_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Project not found"

    def __init__(self, *, project_id: NotBlankStr) -> None:
        super().__init__("Project not found")
        self.project_id: NotBlankStr = project_id


class ProjectAgentNotMemberError(EngineError):
    """Agent is not a member of the task's project team.

    The message is deliberately generic to avoid leaking internal
    identifiers.  Attributes are available for structured logs only.

    Attributes:
        project_id: The project the agent attempted to access.
        agent_id: The agent that is not in the project team.
    """

    status_code: ClassVar[int] = 403
    error_code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    default_message: ClassVar[str] = "Agent not authorized for this project"

    def __init__(
        self,
        *,
        project_id: NotBlankStr,
        agent_id: NotBlankStr,
    ) -> None:
        super().__init__("Agent not authorized for this project")
        self.project_id: NotBlankStr = project_id
        self.agent_id: NotBlankStr = agent_id


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

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.TASK_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Task not found"


class TaskVersionConflictError(TaskMutationError):
    """Raised when optimistic concurrency version does not match."""

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.TASK_VERSION_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Task version conflict"


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


class DelegationRoundLimitError(EngineError):
    """Hard abort when delegation rounds exceed 2x the soft cap.

    Attributes:
        current_round: The round number that triggered the abort.
        soft_limit: The configured soft cap on delegation rounds.
    """

    def __init__(self, current_round: int, soft_limit: int) -> None:
        self.current_round = current_round
        self.soft_limit = soft_limit
        super().__init__(
            f"Delegation round {current_round} exceeds hard limit "
            f"({soft_limit * 2}, soft cap {soft_limit})"
        )


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


class WorkflowExecutionError(EngineError):
    """Base exception for workflow execution failures."""


class WorkflowDefinitionInvalidError(WorkflowExecutionError):
    """Raised when a workflow definition fails validation at activation time."""


class WorkflowConditionEvalError(WorkflowExecutionError):
    """Raised when a condition expression cannot be evaluated."""


class WorkflowExecutionNotFoundError(WorkflowExecutionError):
    """Raised when a workflow execution instance is not found."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.WORKFLOW_EXECUTION_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Workflow execution not found"


class SubworkflowNotFoundError(WorkflowExecutionError):
    """Raised when a referenced subworkflow version cannot be resolved.

    Attributes:
        subworkflow_id: The subworkflow identifier.
        version: The semver pin that failed to resolve.
    """

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.SUBWORKFLOW_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Subworkflow not found"

    def __init__(
        self,
        message: str,
        *,
        subworkflow_id: NotBlankStr,
        version: NotBlankStr,
    ) -> None:
        super().__init__(message)
        self.subworkflow_id = subworkflow_id
        self.version = version


class SubworkflowCycleError(WorkflowExecutionError):
    """Raised when the subworkflow reference graph contains a cycle.

    Attributes:
        cycle_path: Ordered ``(subworkflow_id, version)`` tuples that
            participate in the cycle.
    """

    def __init__(
        self,
        message: str,
        *,
        cycle_path: tuple[tuple[str, str], ...],
    ) -> None:
        super().__init__(message)
        self.cycle_path = cycle_path


class SubworkflowDepthExceededError(WorkflowExecutionError):
    """Raised when runtime subworkflow nesting exceeds the configured limit.

    Attributes:
        depth: The depth at which the limit was exceeded.
        max_depth: The configured maximum.
    """

    def __init__(
        self,
        message: str,
        *,
        depth: int,
        max_depth: int,
    ) -> None:
        super().__init__(message)
        self.depth = depth
        self.max_depth = max_depth


class SubworkflowIOError(WorkflowExecutionError):
    """Raised when subworkflow input or output binding is invalid.

    Covers missing required inputs, unknown inputs, unknown outputs,
    type mismatches, and invalid binding expressions.
    """


class SelfReviewError(EngineError):
    """Raised when an agent attempts to review their own work.

    Structurally prevents an agent from acting as reviewer on a task
    they executed, enforcing separation of duties at the approval gate.

    The exception message is deliberately generic ("Self-review is not
    permitted") to avoid leaking internal agent/task identifiers across
    authorization boundaries when the message is surfaced via an HTTP
    error response.  The ``task_id`` and ``agent_id`` attributes are
    available for structured logs but must NOT be passed to user-facing
    error responses.

    Attributes:
        task_id: The task identifier the self-review was attempted on.
        agent_id: The agent identifier that is both executor and reviewer.
    """

    status_code: ClassVar[int] = 403
    error_code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    default_message: ClassVar[str] = "Self-review is not permitted"

    def __init__(
        self,
        *,
        task_id: NotBlankStr,
        agent_id: NotBlankStr,
    ) -> None:
        super().__init__("Self-review is not permitted")
        self.task_id: NotBlankStr = task_id
        self.agent_id: NotBlankStr = agent_id
