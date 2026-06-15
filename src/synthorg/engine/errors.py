"""Engine-layer error hierarchy."""

from typing import TYPE_CHECKING, ClassVar

from synthorg.core.domain_errors import (
    DomainError,
    NotFoundError,
    VersionConflictError,
)
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.core.types import NotBlankStr

if TYPE_CHECKING:
    # Cycle-breaker: coordination.models imports engine.errors, so a
    # module-level import here would close an import cycle.
    from synthorg.engine.coordination.models import CoordinationPhaseResult


class EngineError(DomainError):
    """Base exception for all engine-layer errors.

    Inherits from :class:`DomainError` so the prefix-vs-category
    validator runs on every subclass; a typo in a subclass
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


class RecoveryConfigError(EngineError):
    """Configuration cannot satisfy the selected recovery strategy.

    Typical cause: ``EngineRecoveryConfig.strategy == CHECKPOINT`` but
    no :class:`CheckpointRepository` was wired through to the factory.
    """


class ProjectNotFoundError(EngineError):
    """Referenced project does not exist.

    The single not-found error for a missing project, raised from both
    the engine lookup and work-pipeline intake paths. The ``project_id``
    attribute is for structured logs only and must NOT be surfaced to
    clients; the wire message stays the generic ``default_message``.
    """

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.PROJECT_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Project not found"

    def __init__(self, *, project_id: NotBlankStr | None = None) -> None:
        super().__init__(self.default_message)
        self.project_id: NotBlankStr | None = project_id


class ProjectAgentNotMemberError(EngineError):
    """Agent is not a member of the task's project team.

    The wire message stays generic to avoid leaking identifiers; the
    ``project_id`` / ``agent_id`` attributes are for structured logs only.
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


class WorkspacePushError(WorkspaceError):
    """Raised when the coordinator-owned push to the git backend fails.

    Distinct from :class:`WorkspaceMergeError` (local git merge state)
    so callers can tell a forge/remote push rejection apart from a
    local textual merge conflict.
    """

    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Failed to push workspace to git backend"


class ProjectWorkspaceError(EngineError):
    """Base exception for persistent project-workspace failures."""


class ProjectWorkspaceNotProvisionedError(ProjectWorkspaceError):
    """Raised when a project workspace is required but not yet provisioned.

    The wire message stays generic to avoid leaking identifiers; the
    ``project_id`` attribute is for structured logs only and must NOT be
    surfaced to clients.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.PROJECT_WORKSPACE_NOT_PROVISIONED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Project workspace not provisioned"

    def __init__(self, *, project_id: NotBlankStr) -> None:
        super().__init__("Project workspace not provisioned")
        self.project_id: NotBlankStr = project_id


class GitBackendError(EngineError):
    """Base exception for pluggable git-backend failures."""


class GitBackendConfigError(GitBackendError):
    """Raised when git-backend configuration is invalid for the strategy.

    Fail-fast at factory construction (e.g. ``LOCAL_PATH`` selected but
    no ``local_repo_path``, or ``EXTERNAL_REMOTE`` without its connection
    catalog / secret-backend dependency).
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.REQUEST_VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Git backend configuration invalid"


class GitBackendProvisionError(GitBackendError):
    """Raised when the git backend fails to provision a repository."""

    default_message: ClassVar[str] = "Repository provisioning failed"


class GitBackendSeedError(GitBackendError):
    """Raised when the git backend fails to seed an existing source.

    Seeding is the one-shot import of an existing repository (clone of a
    remote URL or copy of a local path) into a freshly provisioned
    workspace. Distinct from provisioning (which creates an empty repo):
    a seed onto a workspace that already holds a git history fails here,
    and the brownfield intake service maps that to its own typed error.
    """

    default_message: ClassVar[str] = "Repository seeding failed"


class GitBackendPushError(GitBackendError):
    """Raised when the git backend fails to push a branch."""

    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Git push failed"


class GitBackendFetchError(GitBackendError):
    """Raised when the git backend fails to fetch from the remote."""

    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Git fetch failed"


class GitBackendRemoteMissingError(GitBackendError):
    """Raised when a push targets a forge repo that does not exist yet.

    Distinct from a transient push failure: the operator's credential
    is valid but the addressed repository has never been created. The
    external-remote backend catches this to trigger lazy forge-API
    repo provisioning (create-then-retry-once); it is NOT retried by
    the transient-I/O retry handler.
    """

    default_message: ClassVar[str] = "Remote repository does not exist"


class GitBackendRateLimitError(GitBackendError):
    """Raised when a forge rate-limits a git or forge-API operation.

    Retryable via the transient-I/O backoff handler. ``retry_after``
    carries the server-advertised cooldown (seconds) when present, for
    observability; the backoff itself is exponential (Pattern A).
    """

    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Forge rate limit exceeded"

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after: float | None = retry_after


class GitBackendForgeApiError(GitBackendError):
    """Raised when a forge REST API call fails (non-auth).

    Retryable: forge-API 5xx / connection failures are transient.
    """

    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Forge API request failed"


class GitBackendForgeAuthError(GitBackendForgeApiError):
    """Raised on 401/403 forge-API responses (invalid/expired token).

    Non-retryable: a fresh credential is required, not a backoff.
    """

    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 401
    error_code: ClassVar[ErrorCode] = ErrorCode.UNAUTHORIZED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    default_message: ClassVar[str] = "Forge API authentication failed"


class ProjectEnvironmentError(EngineError):
    """Base exception for reproducible per-project environment failures.

    Named ``ProjectEnvironmentError`` (not ``EnvironmentError``) to avoid
    shadowing the built-in ``EnvironmentError`` alias of ``OSError``;
    mirrors the :class:`ProjectWorkspaceError` sibling.
    """

    error_code: ClassVar[ErrorCode] = ErrorCode.ENVIRONMENT_ERROR


class EnvironmentConfigError(ProjectEnvironmentError):
    """Raised when environment configuration is invalid for the strategy.

    Fail-fast at factory construction (e.g. a strategy selected without
    the runtime dependency it requires, mirroring
    :class:`GitBackendConfigError`).
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.REQUEST_VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Environment configuration invalid"


class EnvironmentProvisionError(ProjectEnvironmentError):
    """Raised when an environment strategy fails to provision."""

    error_code: ClassVar[ErrorCode] = ErrorCode.ENVIRONMENT_PROVISION_FAILED
    default_message: ClassVar[str] = "Environment provisioning failed"


class EnvironmentDockerBuildError(EnvironmentProvisionError):
    """Raised when the devcontainer image build fails."""

    error_code: ClassVar[ErrorCode] = ErrorCode.ENVIRONMENT_DOCKER_BUILD_FAILED
    default_message: ClassVar[str] = "Environment image build failed"


class EnvironmentBackendUnavailableError(ProjectEnvironmentError):
    """Raised when a declaration needs a sandbox backend that is not active.

    Loud, never silent: e.g. a ``DEVCONTAINER`` declaration on a project
    whose build/test categories resolve to the subprocess backend cannot
    build a sealed image, so provisioning fails rather than degrading to
    an unfaithful host-only run.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.ENVIRONMENT_BACKEND_UNAVAILABLE
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Environment backend unavailable"


class TaskEngineError(EngineError):
    """Base exception for all task engine errors."""


class TaskEngineNotRunningError(TaskEngineError):
    """Raised when a mutation is submitted to a stopped task engine."""

    status_code: ClassVar[int] = 503
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Service temporarily unavailable"


class TaskEngineQueueFullError(TaskEngineError):
    """Raised when the task engine queue is at capacity."""

    status_code: ClassVar[int] = 503
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Service temporarily unavailable"


class TaskMutationError(TaskEngineError):
    """Raised when a task mutation fails (not found, validation, etc.)."""

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Task mutation invalid"


class TaskNotFoundError(TaskMutationError, NotFoundError):
    """Raised when a task is not found during mutation.

    Multi-inherits :class:`TaskMutationError` (engine-layer family
    catch) and :class:`NotFoundError` (API-layer
    :func:`require_resource_or_404` accepts as ``error_class``).
    """

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

    Sibling of :class:`TaskMutationError`, not a subtype, so a broad
    ``except TaskMutationError`` handler does not accidentally catch
    internal engine faults. Inherits the default 500 / ENGINE_ERROR /
    INTERNAL metadata from :class:`TaskEngineError`.
    """


class DelegationRoundLimitError(EngineError):
    """Hard abort when delegation rounds exceed 2x the soft cap.

    Attributes:
        current_round: The round number that triggered the abort.
        soft_limit: The configured soft cap on delegation rounds.
    """

    def __init__(self, current_round: int, soft_limit: int) -> None:
        self.current_round: int = current_round
        self.soft_limit: int = soft_limit
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
        self.phase: str = phase
        self.partial_phases: tuple[CoordinationPhaseResult, ...] = partial_phases


class RuntimeServicesBuildError(EngineError):
    """Raised when the boot/reinit runtime-services build fails.

    Wraps the underlying failure from ``build_runtime_services`` (provider
    registry, tool registry, agent engine, or coordinator factory) so the
    boot hook and the ``/setup/complete`` controller see a typed domain
    error instead of a raw exception. The original cause is preserved via
    ``raise ... from exc``.
    """


class WorkflowExecutionError(EngineError):
    """Base exception for workflow execution failures."""


class WorkflowDefinitionInvalidError(WorkflowExecutionError):
    """Raised when a workflow definition fails validation at activation time.

    422 + ``REQUEST_VALIDATION_ERROR``: a definition that fails activation-time
    structural checks is a caller-side validation failure surfaced after the
    request reached the engine, not an internal fault. Aligns with
    :class:`WorkflowDefinitionValidationError` (the create/update path) so
    every "invalid workflow definition" surface emits the same 422 envelope.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.REQUEST_VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Invalid workflow definition for activation"


class WorkflowConditionEvalError(WorkflowExecutionError):
    """Raised when a condition expression cannot be evaluated.

    422 + ``REQUEST_VALIDATION_ERROR``: a condition expression that fails
    evaluation is authored by the caller as part of the workflow definition,
    so the failure is a request-shape problem rather than an engine fault.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.REQUEST_VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Workflow condition evaluation failed"


class WorkflowExecutionNotFoundError(WorkflowExecutionError, NotFoundError):
    """Raised when a workflow execution instance is not found.

    Multi-inherits :class:`WorkflowExecutionError` (engine-layer
    family catch) and :class:`NotFoundError`
    (:func:`require_resource_or_404` accepts as ``error_class``).
    """

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
        self.subworkflow_id: NotBlankStr = subworkflow_id
        self.version: NotBlankStr = version


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
        self.cycle_path: tuple[tuple[str, str], ...] = cycle_path


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
        self.depth: int = depth
        self.max_depth: int = max_depth


class SubworkflowIOError(WorkflowExecutionError):
    """Raised when subworkflow input or output binding is invalid.

    Covers missing required inputs, unknown inputs, unknown outputs,
    type mismatches, and invalid binding expressions. The 422 mapping
    treats binding mismatches as caller-side validation failures so
    the centralised RFC 9457 dispatch surfaces a structured envelope
    without controller-level translation.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.REQUEST_VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Subworkflow input/output validation failed"


class WorkflowTypeInvalidError(WorkflowExecutionError):
    """Raised when a request specifies an unknown ``workflow_type`` value.

    Uses ``REQUEST_VALIDATION_ERROR`` (2001) and 400 to align with the
    other request-shape failures in this module: the value did not parse
    against the ``WorkflowType`` enum at the API boundary.
    """

    status_code: ClassVar[int] = 400
    error_code: ClassVar[ErrorCode] = ErrorCode.REQUEST_VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Invalid workflow type"


class WorkflowDefinitionValidationError(WorkflowExecutionError):
    """Raised when a workflow definition fails structural checks.

    The default message is intentionally generic so Pydantic validation
    detail does not leak to API clients; callers may still chain the
    underlying exception with ``raise … from exc`` for the structured
    log emitted by the centralised handler.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.REQUEST_VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Invalid workflow definition."


class WorkflowYamlExportError(WorkflowExecutionError):
    """Raised when YAML serialisation of a workflow definition fails.

    Maps to 422 (Unprocessable Entity) on ``/workflows/{id}/export``:
    the request itself is well-formed, but the persisted definition
    cannot be serialised to YAML -- a content-level failure rather
    than a request-syntax problem.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.REQUEST_VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Workflow YAML export failed"


class WorkflowExecutionAlreadyTerminalError(VersionConflictError):
    """Raised when cancel targets an execution already in a terminal status.

    Distinct from :class:`synthorg.core.domain_errors.VersionConflictError`
    (4002) so API clients can discriminate "the execution finished before
    you cancelled" (no retry will succeed) from a row-level optimistic-
    concurrency race where the caller can re-read and try again. Both
    map to 409 + ``CONFLICT`` so the HTTP envelope shape is unchanged;
    only the ``error_code`` differs.
    """

    error_code: ClassVar[ErrorCode] = ErrorCode.WORKFLOW_EXECUTION_ALREADY_TERMINAL
    default_message: ClassVar[str] = (
        "Workflow execution is already in a terminal status"
    )


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
