# module-kind: declarative
"""Engine-layer error hierarchy."""

from typing import TYPE_CHECKING, ClassVar

from synthorg.core.domain_errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
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


class PlanReviewUnavailableError(EngineError):
    """Raised when a seated review panel could not review at all.

    Distinct from a quiet panel: every seated reviewer's provider failed, so
    the plan carries no quality signal for a reason that is an outage, not a
    judgement. Parking it would present an unreviewed plan as an
    unobjectionable one, so plan preparation fails instead.
    """

    error_code: ClassVar[ErrorCode] = ErrorCode.PLAN_REVIEW_UNAVAILABLE
    default_message: ClassVar[str] = "Plan review panel could not run"


class DecompositionCycleError(DecompositionError):
    """Raised when a dependency cycle is detected in the subtask graph."""


class DecompositionDepthError(DecompositionError):
    """Raised when decomposition exceeds the maximum nesting depth."""


class DecompositionSubtaskLimitError(DecompositionError):
    """Raised when a plan carries more subtasks than the caller allowed.

    Every strategy refuses an over-limit plan rather than substituting a
    smaller one: the request named the ceiling, and quietly returning a
    thinner plan the operator never saw is a worse answer wearing a success.

    Both numbers are attributes, not only prose, so a caller can offer to
    raise the ceiling to the number actually produced without parsing the
    message. Composing the message here also keeps the three strategies from
    wording the same refusal differently.
    """

    def __init__(self, *, produced: int, limit: int) -> None:
        super().__init__(
            f"Plan has {produced} subtasks, exceeds max_subtasks of {limit}"
        )
        self.produced: int = produced
        self.limit: int = limit


class RetrospectiveError(EngineError):
    """Base exception for objective-retrospective capture failures."""


class RetrospectiveParseError(RetrospectiveError):
    """Raised when a submitted retrospective cannot be parsed."""


class InitiativeEvaluationError(EngineError):
    """Base exception for initiative-evaluation failures."""


class InitiativeEvaluationParseError(InitiativeEvaluationError):
    """Raised when a submitted evaluation cannot be parsed."""


class PlanReviewError(EngineError):
    """Base exception for stakeholder plan-review failures."""


class PlanReviewParseError(PlanReviewError):
    """Raised when a panellist's submitted review cannot be parsed."""


class PlanReviewCategoryGuidanceError(PlanReviewError):
    """Raised when a finding category carries no reviewer-facing meaning.

    The brief and the tool schema render the vocabulary from one mapping, so a
    category present in the enum and absent from that mapping would reach a
    reviewer as a bare name. A reviewer shown a name it was never told the
    sense of proposes its own, which is the behaviour the vocabulary exists to
    remove, so the render fails rather than shipping a half-explained list.
    """


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


class RecoveryCheckpointMissingError(EngineError):
    """A resumable recovery result carries no checkpoint to resume from.

    The strategy answered ``can_resume`` true and then supplied no
    checkpoint JSON, so the two halves of its own answer disagree. Typed
    rather than a bare ``RuntimeError`` because the recovery boundary
    catches broadly: an untyped breach degrades into one warning line
    indistinguishable from any other failure the resume path hit.
    """


class ParkedContextRepoMissingError(EngineError):
    """A context was parked with nowhere to persist it.

    Raised rather than returning quietly, because a park that stores
    nothing is a run reported PARKED that no resume can ever find: the
    approval waits for a decision, the decision looks up a parked context
    that was never written, and the run's only remaining exit is a manual
    cancellation nobody knows to perform. Every caller already has a
    honest fallback for a failed park (a hard-ceiling crossing stops the
    run as BUDGET_EXHAUSTED, a tool escalation denies), so failing loud
    costs a real behaviour and buys back a reachable one.
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


class ProjectRepositoryNotConfiguredError(EngineError):
    """Task declares a project but no project repository is configured.

    Fail-loud precondition: with no project repository wired the engine
    cannot validate the task's project membership or budget, so it must
    not run the agent unvalidated. Raised into the engine's fatal-error
    boundary so the task terminates FAILED with the surfaced reason. The
    ``project_id`` attribute is for structured logs only.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.PROJECT_REPOSITORY_NOT_CONFIGURED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Project repository not configured"

    def __init__(self, *, project_id: NotBlankStr | None = None) -> None:
        super().__init__(self.default_message)
        self.project_id: NotBlankStr | None = project_id


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
    error_code: ClassVar[ErrorCode] = ErrorCode.GIT_BACKEND_CONFIG_INVALID
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
    error_code: ClassVar[ErrorCode] = ErrorCode.ENVIRONMENT_CONFIG_INVALID
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
    error_code: ClassVar[ErrorCode] = ErrorCode.TASK_ENGINE_NOT_RUNNING
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    retryable: ClassVar[bool] = True
    # Sanitized default: a 503 must not leak internal engine state to the
    # client; the machine-branchable distinction is carried by error_code.
    default_message: ClassVar[str] = "Service temporarily unavailable"


class TaskEngineQueueFullError(TaskEngineError):
    """Raised when the task engine queue is at capacity."""

    status_code: ClassVar[int] = 503
    error_code: ClassVar[ErrorCode] = ErrorCode.TASK_ENGINE_QUEUE_FULL
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    retryable: ClassVar[bool] = True
    # Sanitized default: a 503 must not leak internal queue state to the
    # client; the machine-branchable distinction is carried by error_code.
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
    # A stale-version write lost an optimistic-concurrency race; re-reading and
    # retrying against the current version is the correct client response.
    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Task version conflict"


class TaskOrphanedPlanError(TaskEngineError):
    """A task names a plan that no longer exists.

    Filing it would leave live work under nothing: its plan id resolves to
    no row, so the rollup that would notice the work never reaches it. The
    complement of the plan delete's own guard, which refuses to remove a
    plan while live tasks exist.
    """


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


class CoordinationConfigError(CoordinationError):
    """Coordinator configuration is invalid at startup."""


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

    422 + ``WORKFLOW_DEFINITION_INVALID``: a definition that fails
    activation-time structural checks is a caller-side validation failure
    surfaced after the request reached the engine, not an internal fault.
    Distinct from :class:`WorkflowDefinitionValidationError` (the
    create/update path, ``WORKFLOW_DEFINITION_VALIDATION_FAILED``) so a
    client can tell an activation-time rejection from a create/update one;
    both stay in the 422 VALIDATION category.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.WORKFLOW_DEFINITION_INVALID
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Invalid workflow definition for activation"


class WorkflowConditionEvalError(WorkflowExecutionError):
    """Raised when a condition expression cannot be evaluated.

    422 + ``WORKFLOW_CONDITION_EVAL_FAILED``: a condition expression that
    fails evaluation is authored by the caller as part of the workflow
    definition, so the failure is a request-shape problem rather than an
    engine fault.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.WORKFLOW_CONDITION_EVAL_FAILED
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

    error_code: ClassVar[ErrorCode] = ErrorCode.SUBWORKFLOW_CYCLE_ERROR

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

    error_code: ClassVar[ErrorCode] = ErrorCode.SUBWORKFLOW_DEPTH_EXCEEDED_ERROR

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
    error_code: ClassVar[ErrorCode] = ErrorCode.SUBWORKFLOW_IO_INVALID
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Subworkflow input/output validation failed"


class WorkflowTypeInvalidError(WorkflowExecutionError):
    """Raised when a request specifies an unknown ``workflow_type`` value.

    Uses ``WORKFLOW_TYPE_INVALID`` and 400: the value did not parse
    against the ``WorkflowType`` enum at the API boundary, a request-shape
    failure distinct from the workflow-definition validation codes.
    """

    status_code: ClassVar[int] = 400
    error_code: ClassVar[ErrorCode] = ErrorCode.WORKFLOW_TYPE_INVALID
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
    error_code: ClassVar[ErrorCode] = ErrorCode.WORKFLOW_DEFINITION_VALIDATION_FAILED
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
    error_code: ClassVar[ErrorCode] = ErrorCode.WORKFLOW_YAML_EXPORT_FAILED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Workflow YAML export failed"


class KanbanInvalidMoveError(EngineError):
    """Raised when a requested Kanban column move is not a legal transition.

    Maps to 400: the target column is unreachable from the card's current
    column under ``VALID_COLUMN_TRANSITIONS`` (e.g. a jump that skips the
    board's flow), a request-shape failure surfaced by the board service.
    """

    status_code: ClassVar[int] = 400
    error_code: ClassVar[ErrorCode] = ErrorCode.KANBAN_INVALID_MOVE
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Invalid Kanban board move"


class KanbanWipLimitError(EngineError):
    """Raised when a move would push a column past its enforced WIP limit.

    Maps to 409 (conflict): the move is legal but the target column is at
    capacity and WIP enforcement is on, so the board rejects it until a
    slot frees. Advisory mode never raises this.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.KANBAN_WIP_LIMIT_EXCEEDED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Kanban column is at its WIP limit"


class SprintError(EngineError):
    """Base for agile-sprint service failures."""


class SprintNotFoundError(SprintError, NotFoundError):
    """Raised when a sprint id resolves to no persisted row.

    Maps to 404: the requested sprint does not exist.
    """

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.SPRINT_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Sprint not found"


class SprintBacklogFullError(SprintError, ConflictError):
    """Raised when adding a task would exceed ``max_tasks_per_sprint``.

    Maps to 409 (conflict): the sprint backlog is at capacity, so the
    task belongs in a later sprint until a slot frees.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.SPRINT_BACKLOG_FULL
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Sprint backlog is full"


class SprintTransitionConflictError(SprintError, ConflictError):
    """Raised when a sprint is not in the state a lifecycle hop requires.

    Maps to 409 (conflict). Fires from two places: an upfront status
    check (e.g. ``add_task`` / ``start_sprint`` on a non-``PLANNING``
    sprint, or advancing a terminal sprint), and the ``transition_if``
    CAS returning a mismatch when a concurrent advance moved the row out
    of the expected ``from`` state before this hop landed.
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.SPRINT_TRANSITION_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Sprint is not in the expected state"


class SprintTaskNotInBacklogError(SprintError, ValidationError):
    """Raised when work is requested on a task outside the active sprint.

    Maps to 400: the board move targets a task that is not in the active
    sprint's backlog, so the sprint gate rejects pulling it into flow.
    """

    status_code: ClassVar[int] = 400
    error_code: ClassVar[ErrorCode] = ErrorCode.SPRINT_TASK_NOT_IN_BACKLOG
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Task is not in the active sprint backlog"


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
