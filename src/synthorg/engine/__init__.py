# module-kind: declarative
"""Agent execution engine.

Re-exports the public API for the agent orchestrator, task engine,
run results, system prompt construction, runtime execution state,
execution loops, and engine errors.
"""

import threading
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from synthorg.approval.models import (
        EscalationInfo,
        ResumePayload,
    )
    from synthorg.engine.agent_engine import AgentEngine
    from synthorg.engine.agent_state import AgentRuntimeState
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.engine.assignment import (
        STRATEGY_MAP,
        STRATEGY_NAME_AUCTION,
        STRATEGY_NAME_COST_OPTIMIZED,
        STRATEGY_NAME_HIERARCHICAL,
        STRATEGY_NAME_LOAD_BALANCED,
        STRATEGY_NAME_MANUAL,
        STRATEGY_NAME_ROLE_BASED,
        AgentWorkload,
        AssignmentCandidate,
        AssignmentRequest,
        AssignmentResult,
        AuctionBidRanker,
        CandidatePoolFilter,
        CandidateRanker,
        CostDescendingRanker,
        HierarchicalPoolFilter,
        IdentityPoolFilter,
        ManualAssignmentStrategy,
        PoolFilterResult,
        RankingResult,
        ScoreDescendingRanker,
        ScoringBasedAssignmentStrategy,
        TaskAssignmentService,
        TaskAssignmentStrategy,
        WorkloadAscendingRanker,
        build_strategy_map,
    )
    from synthorg.engine.checkpoint import (
        Checkpoint,
        CheckpointCallback,
        CheckpointConfig,
        Heartbeat,
        make_checkpoint_callback,
    )
    from synthorg.engine.checkpoint.strategy import CheckpointRecoveryStrategy
    from synthorg.engine.classification import (
        ClassificationResult,
        ErrorFinding,
        ErrorSeverity,
        classify_execution_errors,
    )
    from synthorg.engine.context import AgentContext
    from synthorg.engine.context_snapshot import AgentContextSnapshot
    from synthorg.engine.coordination import (
        AgentContribution,
        ContextDependentDispatcher,
        CoordinationConfig,
        CoordinationContext,
        CoordinationPhaseResult,
        CoordinationResult,
        CoordinationResultWithAttribution,
        CoordinationWave,
        DispatchResult,
        MultiAgentCoordinator,
        SasDispatcher,
        TopologyDispatcher,
        WaveDispatcher,
        build_execution_waves,
        select_dispatcher,
    )
    from synthorg.engine.decomposition import (
        DecompositionContext,
        DecompositionPlan,
        DecompositionResult,
        DecompositionService,
        DecompositionStrategy,
        DependencyGraph,
        LlmDecompositionConfig,
        LlmDecompositionStrategy,
        ManualDecompositionStrategy,
        StatusRollup,
        SubtaskDefinition,
        SubtaskStatusRollup,
        TaskStructureClassifier,
    )
    from synthorg.engine.errors import (
        CoordinationError,
        CoordinationPhaseError,
        DecompositionCycleError,
        DecompositionDepthError,
        DecompositionError,
        DecompositionSubtaskLimitError,
        EngineError,
        ExecutionStateError,
        LoopExecutionError,
        MaxTurnsExceededError,
        NoEligibleAgentError,
        ParallelExecutionError,
        PromptBuildError,
        ResourceConflictError,
        TaskAssignmentError,
        TaskEngineError,
        TaskEngineNotRunningError,
        TaskEngineQueueFullError,
        TaskInternalError,
        TaskMutationError,
        TaskNotFoundError,
        TaskRoutingError,
        TaskVersionConflictError,
        WorkspaceCleanupError,
        WorkspaceError,
        WorkspaceLimitError,
        WorkspaceMergeError,
        WorkspaceSetupError,
    )
    from synthorg.engine.loop_budget_defaults import DEFAULT_MAX_TURNS
    from synthorg.engine.loop_protocol import (
        BudgetChecker,
        ExecutionLoop,
        ExecutionResult,
        ShutdownChecker,
        TerminationReason,
    )
    from synthorg.engine.metrics import TaskCompletionMetrics
    from synthorg.engine.middleware.coordination_protocol import (
        BaseCoordinationMiddleware,
        CoordinationMiddleware,
        CoordinationMiddlewareChain,
        CoordinationMiddlewareContext,
    )
    from synthorg.engine.middleware.errors import (
        ClarificationRequiredError,
        MiddlewareConfigError,
        MiddlewareError,
        MiddlewareRegistryError,
    )
    from synthorg.engine.middleware.models import (
        AgentMiddlewareContext,
        AssumptionViolationEvent,
        AssumptionViolationType,
        ModelCallResult,
        TaskLedger,
        ToolCallResult,
    )
    from synthorg.engine.middleware.protocol import (
        AgentMiddleware,
        AgentMiddlewareChain,
        BaseAgentMiddleware,
    )
    from synthorg.engine.parallel import (
        ParallelExecutor,
        ProgressCallback,
    )
    from synthorg.engine.parallel_models import (
        AgentAssignment,
        AgentOutcome,
        ParallelExecutionGroup,
        ParallelExecutionResult,
        ParallelProgress,
    )
    from synthorg.engine.prompt import (
        SystemPrompt,
        build_system_prompt,
    )
    from synthorg.engine.react_loop import ReactLoop
    from synthorg.engine.recovery import (
        FailAndReassignStrategy,
        RecoveryResult,
        RecoveryStrategy,
    )
    from synthorg.engine.resource_lock import (
        InMemoryResourceLock,
        ResourceLock,
    )
    from synthorg.engine.routing import (
        AgentTaskScorer,
        AutoTopologyConfig,
        RoutingCandidate,
        RoutingDecision,
        RoutingResult,
        TaskRoutingService,
        TopologySelector,
    )
    from synthorg.engine.run_result import AgentRunResult
    from synthorg.engine.shutdown import (
        CheckpointSaver,
        CleanupCallback,
        CooperativeTimeoutStrategy,
        ShutdownManager,
        ShutdownResult,
        ShutdownStrategy,
    )
    from synthorg.engine.shutdown_checkpoint import CheckpointAndStopStrategy
    from synthorg.engine.shutdown_finish_tool import FinishCurrentToolStrategy
    from synthorg.engine.shutdown_immediate import ImmediateCancelStrategy
    from synthorg.engine.shutdown_strategies import build_shutdown_strategy
    from synthorg.engine.stagnation import (
        StagnationConfig,
        StagnationDetector,
        StagnationResult,
        StagnationVerdict,
        ToolRepetitionDetector,
    )
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.engine.task_engine_config import TaskEngineConfig
    from synthorg.engine.task_engine_models import (
        CancelTaskMutation,
        CreateTaskData,
        CreateTaskMutation,
        DeleteTaskMutation,
        TaskMutation,
        TaskMutationResult,
        TaskStateChanged,
        TransitionTaskMutation,
        UpdateTaskMutation,
    )
    from synthorg.engine.task_execution import (
        StatusTransition,
        TaskExecution,
    )
    from synthorg.engine.token_estimation import (
        DefaultTokenEstimator,
        PromptTokenEstimator,
    )
    from synthorg.engine.workspace import (
        MergeConflict,
        MergeOrchestrator,
        MergeResult,
        PlannerWorktreesConfig,
        PlannerWorktreeStrategy,
        Workspace,
        WorkspaceGroupResult,
        WorkspaceIsolationConfig,
        WorkspaceIsolationService,
        WorkspaceIsolationStrategy,
        WorkspaceRequest,
    )
    from synthorg.providers.models import (
        ZERO_TOKEN_USAGE,
        add_token_usage,
    )

# name -> (module path, attribute) for PEP 562 lazy resolution. The engine hub
# eagerly re-exported ~200 symbols spanning the whole agent orchestrator, so
# importing any light ``engine.*`` leaf (e.g. ``engine.workspace.enums``) pulled
# the entire engine + communication graph and helped close a cross-package
# import cycle (ADR-0012). Resolving on first access keeps
# ``from synthorg.engine import AgentEngine`` working unchanged.
_LAZY_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "EscalationInfo": ("synthorg.approval.models", "EscalationInfo"),
    "ResumePayload": ("synthorg.approval.models", "ResumePayload"),
    "AgentEngine": ("synthorg.engine.agent_engine", "AgentEngine"),
    "AgentRuntimeState": ("synthorg.engine.agent_state", "AgentRuntimeState"),
    "ApprovalGate": ("synthorg.engine.approval_gate", "ApprovalGate"),
    "STRATEGY_MAP": ("synthorg.engine.assignment", "STRATEGY_MAP"),
    "STRATEGY_NAME_AUCTION": (
        "synthorg.engine.assignment",
        "STRATEGY_NAME_AUCTION",
    ),
    "STRATEGY_NAME_COST_OPTIMIZED": (
        "synthorg.engine.assignment",
        "STRATEGY_NAME_COST_OPTIMIZED",
    ),
    "STRATEGY_NAME_HIERARCHICAL": (
        "synthorg.engine.assignment",
        "STRATEGY_NAME_HIERARCHICAL",
    ),
    "STRATEGY_NAME_LOAD_BALANCED": (
        "synthorg.engine.assignment",
        "STRATEGY_NAME_LOAD_BALANCED",
    ),
    "STRATEGY_NAME_MANUAL": ("synthorg.engine.assignment", "STRATEGY_NAME_MANUAL"),
    "STRATEGY_NAME_ROLE_BASED": (
        "synthorg.engine.assignment",
        "STRATEGY_NAME_ROLE_BASED",
    ),
    "AgentWorkload": ("synthorg.engine.assignment", "AgentWorkload"),
    "AssignmentCandidate": ("synthorg.engine.assignment", "AssignmentCandidate"),
    "AssignmentRequest": ("synthorg.engine.assignment", "AssignmentRequest"),
    "AssignmentResult": ("synthorg.engine.assignment", "AssignmentResult"),
    "AuctionBidRanker": ("synthorg.engine.assignment", "AuctionBidRanker"),
    "CandidatePoolFilter": ("synthorg.engine.assignment", "CandidatePoolFilter"),
    "CandidateRanker": ("synthorg.engine.assignment", "CandidateRanker"),
    "CostDescendingRanker": ("synthorg.engine.assignment", "CostDescendingRanker"),
    "HierarchicalPoolFilter": (
        "synthorg.engine.assignment",
        "HierarchicalPoolFilter",
    ),
    "IdentityPoolFilter": ("synthorg.engine.assignment", "IdentityPoolFilter"),
    "ManualAssignmentStrategy": (
        "synthorg.engine.assignment",
        "ManualAssignmentStrategy",
    ),
    "PoolFilterResult": ("synthorg.engine.assignment", "PoolFilterResult"),
    "RankingResult": ("synthorg.engine.assignment", "RankingResult"),
    "ScoreDescendingRanker": (
        "synthorg.engine.assignment",
        "ScoreDescendingRanker",
    ),
    "ScoringBasedAssignmentStrategy": (
        "synthorg.engine.assignment",
        "ScoringBasedAssignmentStrategy",
    ),
    "TaskAssignmentService": (
        "synthorg.engine.assignment",
        "TaskAssignmentService",
    ),
    "TaskAssignmentStrategy": (
        "synthorg.engine.assignment",
        "TaskAssignmentStrategy",
    ),
    "WorkloadAscendingRanker": (
        "synthorg.engine.assignment",
        "WorkloadAscendingRanker",
    ),
    "build_strategy_map": ("synthorg.engine.assignment", "build_strategy_map"),
    "Checkpoint": ("synthorg.engine.checkpoint", "Checkpoint"),
    "CheckpointCallback": ("synthorg.engine.checkpoint", "CheckpointCallback"),
    "CheckpointConfig": ("synthorg.engine.checkpoint", "CheckpointConfig"),
    "CheckpointRecoveryStrategy": (
        "synthorg.engine.checkpoint.strategy",
        "CheckpointRecoveryStrategy",
    ),
    "Heartbeat": ("synthorg.engine.checkpoint", "Heartbeat"),
    "make_checkpoint_callback": (
        "synthorg.engine.checkpoint",
        "make_checkpoint_callback",
    ),
    "ClassificationResult": (
        "synthorg.engine.classification",
        "ClassificationResult",
    ),
    "ErrorFinding": ("synthorg.engine.classification", "ErrorFinding"),
    "ErrorSeverity": ("synthorg.engine.classification", "ErrorSeverity"),
    "classify_execution_errors": (
        "synthorg.engine.classification",
        "classify_execution_errors",
    ),
    "DEFAULT_MAX_TURNS": (
        "synthorg.engine.loop_budget_defaults",
        "DEFAULT_MAX_TURNS",
    ),
    "AgentContext": ("synthorg.engine.context", "AgentContext"),
    "AgentContextSnapshot": (
        "synthorg.engine.context_snapshot",
        "AgentContextSnapshot",
    ),
    "AgentContribution": ("synthorg.engine.coordination", "AgentContribution"),
    "ContextDependentDispatcher": (
        "synthorg.engine.coordination",
        "ContextDependentDispatcher",
    ),
    "CoordinationConfig": ("synthorg.engine.coordination", "CoordinationConfig"),
    "CoordinationContext": ("synthorg.engine.coordination", "CoordinationContext"),
    "CoordinationPhaseResult": (
        "synthorg.engine.coordination",
        "CoordinationPhaseResult",
    ),
    "CoordinationResult": ("synthorg.engine.coordination", "CoordinationResult"),
    "CoordinationResultWithAttribution": (
        "synthorg.engine.coordination",
        "CoordinationResultWithAttribution",
    ),
    "CoordinationWave": ("synthorg.engine.coordination", "CoordinationWave"),
    "DispatchResult": ("synthorg.engine.coordination", "DispatchResult"),
    "MultiAgentCoordinator": (
        "synthorg.engine.coordination",
        "MultiAgentCoordinator",
    ),
    "SasDispatcher": ("synthorg.engine.coordination", "SasDispatcher"),
    "TopologyDispatcher": ("synthorg.engine.coordination", "TopologyDispatcher"),
    "WaveDispatcher": ("synthorg.engine.coordination", "WaveDispatcher"),
    "build_execution_waves": (
        "synthorg.engine.coordination",
        "build_execution_waves",
    ),
    "select_dispatcher": ("synthorg.engine.coordination", "select_dispatcher"),
    "DecompositionContext": (
        "synthorg.engine.decomposition",
        "DecompositionContext",
    ),
    "DecompositionPlan": ("synthorg.engine.decomposition", "DecompositionPlan"),
    "DecompositionResult": ("synthorg.engine.decomposition", "DecompositionResult"),
    "DecompositionService": (
        "synthorg.engine.decomposition",
        "DecompositionService",
    ),
    "DecompositionStrategy": (
        "synthorg.engine.decomposition",
        "DecompositionStrategy",
    ),
    "DependencyGraph": ("synthorg.engine.decomposition", "DependencyGraph"),
    "LlmDecompositionConfig": (
        "synthorg.engine.decomposition",
        "LlmDecompositionConfig",
    ),
    "LlmDecompositionStrategy": (
        "synthorg.engine.decomposition",
        "LlmDecompositionStrategy",
    ),
    "ManualDecompositionStrategy": (
        "synthorg.engine.decomposition",
        "ManualDecompositionStrategy",
    ),
    "StatusRollup": ("synthorg.engine.decomposition", "StatusRollup"),
    "SubtaskDefinition": ("synthorg.engine.decomposition", "SubtaskDefinition"),
    "SubtaskStatusRollup": ("synthorg.engine.decomposition", "SubtaskStatusRollup"),
    "TaskStructureClassifier": (
        "synthorg.engine.decomposition",
        "TaskStructureClassifier",
    ),
    "CoordinationError": ("synthorg.engine.errors", "CoordinationError"),
    "CoordinationPhaseError": ("synthorg.engine.errors", "CoordinationPhaseError"),
    "DecompositionCycleError": (
        "synthorg.engine.errors",
        "DecompositionCycleError",
    ),
    "DecompositionDepthError": (
        "synthorg.engine.errors",
        "DecompositionDepthError",
    ),
    "DecompositionError": ("synthorg.engine.errors", "DecompositionError"),
    "DecompositionSubtaskLimitError": (
        "synthorg.engine.errors",
        "DecompositionSubtaskLimitError",
    ),
    "EngineError": ("synthorg.engine.errors", "EngineError"),
    "ExecutionStateError": ("synthorg.engine.errors", "ExecutionStateError"),
    "LoopExecutionError": ("synthorg.engine.errors", "LoopExecutionError"),
    "MaxTurnsExceededError": ("synthorg.engine.errors", "MaxTurnsExceededError"),
    "NoEligibleAgentError": ("synthorg.engine.errors", "NoEligibleAgentError"),
    "ParallelExecutionError": ("synthorg.engine.errors", "ParallelExecutionError"),
    "PromptBuildError": ("synthorg.engine.errors", "PromptBuildError"),
    "ResourceConflictError": ("synthorg.engine.errors", "ResourceConflictError"),
    "TaskAssignmentError": ("synthorg.engine.errors", "TaskAssignmentError"),
    "TaskEngineError": ("synthorg.engine.errors", "TaskEngineError"),
    "TaskEngineNotRunningError": (
        "synthorg.engine.errors",
        "TaskEngineNotRunningError",
    ),
    "TaskEngineQueueFullError": (
        "synthorg.engine.errors",
        "TaskEngineQueueFullError",
    ),
    "TaskInternalError": ("synthorg.engine.errors", "TaskInternalError"),
    "TaskMutationError": ("synthorg.engine.errors", "TaskMutationError"),
    "TaskNotFoundError": ("synthorg.engine.errors", "TaskNotFoundError"),
    "TaskRoutingError": ("synthorg.engine.errors", "TaskRoutingError"),
    "TaskVersionConflictError": (
        "synthorg.engine.errors",
        "TaskVersionConflictError",
    ),
    "WorkspaceCleanupError": ("synthorg.engine.errors", "WorkspaceCleanupError"),
    "WorkspaceError": ("synthorg.engine.errors", "WorkspaceError"),
    "WorkspaceLimitError": ("synthorg.engine.errors", "WorkspaceLimitError"),
    "WorkspaceMergeError": ("synthorg.engine.errors", "WorkspaceMergeError"),
    "WorkspaceSetupError": ("synthorg.engine.errors", "WorkspaceSetupError"),
    "BudgetChecker": ("synthorg.engine.loop_protocol", "BudgetChecker"),
    "ExecutionLoop": ("synthorg.engine.loop_protocol", "ExecutionLoop"),
    "ExecutionResult": ("synthorg.engine.loop_protocol", "ExecutionResult"),
    "ShutdownChecker": ("synthorg.engine.loop_protocol", "ShutdownChecker"),
    "TerminationReason": ("synthorg.engine.loop_protocol", "TerminationReason"),
    "TaskCompletionMetrics": ("synthorg.engine.metrics", "TaskCompletionMetrics"),
    "BaseCoordinationMiddleware": (
        "synthorg.engine.middleware.coordination_protocol",
        "BaseCoordinationMiddleware",
    ),
    "CoordinationMiddleware": (
        "synthorg.engine.middleware.coordination_protocol",
        "CoordinationMiddleware",
    ),
    "CoordinationMiddlewareChain": (
        "synthorg.engine.middleware.coordination_protocol",
        "CoordinationMiddlewareChain",
    ),
    "CoordinationMiddlewareContext": (
        "synthorg.engine.middleware.coordination_protocol",
        "CoordinationMiddlewareContext",
    ),
    "ClarificationRequiredError": (
        "synthorg.engine.middleware.errors",
        "ClarificationRequiredError",
    ),
    "MiddlewareConfigError": (
        "synthorg.engine.middleware.errors",
        "MiddlewareConfigError",
    ),
    "MiddlewareError": ("synthorg.engine.middleware.errors", "MiddlewareError"),
    "MiddlewareRegistryError": (
        "synthorg.engine.middleware.errors",
        "MiddlewareRegistryError",
    ),
    "AgentMiddlewareContext": (
        "synthorg.engine.middleware.models",
        "AgentMiddlewareContext",
    ),
    "AssumptionViolationEvent": (
        "synthorg.engine.middleware.models",
        "AssumptionViolationEvent",
    ),
    "AssumptionViolationType": (
        "synthorg.engine.middleware.models",
        "AssumptionViolationType",
    ),
    "ModelCallResult": ("synthorg.engine.middleware.models", "ModelCallResult"),
    "TaskLedger": ("synthorg.engine.middleware.models", "TaskLedger"),
    "ToolCallResult": ("synthorg.engine.middleware.models", "ToolCallResult"),
    "AgentMiddleware": ("synthorg.engine.middleware.protocol", "AgentMiddleware"),
    "AgentMiddlewareChain": (
        "synthorg.engine.middleware.protocol",
        "AgentMiddlewareChain",
    ),
    "BaseAgentMiddleware": (
        "synthorg.engine.middleware.protocol",
        "BaseAgentMiddleware",
    ),
    "ParallelExecutor": ("synthorg.engine.parallel", "ParallelExecutor"),
    "ProgressCallback": ("synthorg.engine.parallel", "ProgressCallback"),
    "AgentAssignment": ("synthorg.engine.parallel_models", "AgentAssignment"),
    "AgentOutcome": ("synthorg.engine.parallel_models", "AgentOutcome"),
    "ParallelExecutionGroup": (
        "synthorg.engine.parallel_models",
        "ParallelExecutionGroup",
    ),
    "ParallelExecutionResult": (
        "synthorg.engine.parallel_models",
        "ParallelExecutionResult",
    ),
    "ParallelProgress": ("synthorg.engine.parallel_models", "ParallelProgress"),
    "SystemPrompt": ("synthorg.engine.prompt", "SystemPrompt"),
    "build_system_prompt": ("synthorg.engine.prompt", "build_system_prompt"),
    "ReactLoop": ("synthorg.engine.react_loop", "ReactLoop"),
    "FailAndReassignStrategy": (
        "synthorg.engine.recovery",
        "FailAndReassignStrategy",
    ),
    "RecoveryResult": ("synthorg.engine.recovery", "RecoveryResult"),
    "RecoveryStrategy": ("synthorg.engine.recovery", "RecoveryStrategy"),
    "InMemoryResourceLock": (
        "synthorg.engine.resource_lock",
        "InMemoryResourceLock",
    ),
    "ResourceLock": ("synthorg.engine.resource_lock", "ResourceLock"),
    "AgentTaskScorer": ("synthorg.engine.routing", "AgentTaskScorer"),
    "AutoTopologyConfig": ("synthorg.engine.routing", "AutoTopologyConfig"),
    "RoutingCandidate": ("synthorg.engine.routing", "RoutingCandidate"),
    "RoutingDecision": ("synthorg.engine.routing", "RoutingDecision"),
    "RoutingResult": ("synthorg.engine.routing", "RoutingResult"),
    "TaskRoutingService": ("synthorg.engine.routing", "TaskRoutingService"),
    "TopologySelector": ("synthorg.engine.routing", "TopologySelector"),
    "AgentRunResult": ("synthorg.engine.run_result", "AgentRunResult"),
    "CheckpointSaver": ("synthorg.engine.shutdown", "CheckpointSaver"),
    "CleanupCallback": ("synthorg.engine.shutdown", "CleanupCallback"),
    "CooperativeTimeoutStrategy": (
        "synthorg.engine.shutdown",
        "CooperativeTimeoutStrategy",
    ),
    "ShutdownManager": ("synthorg.engine.shutdown", "ShutdownManager"),
    "ShutdownResult": ("synthorg.engine.shutdown", "ShutdownResult"),
    "ShutdownStrategy": ("synthorg.engine.shutdown", "ShutdownStrategy"),
    "CheckpointAndStopStrategy": (
        "synthorg.engine.shutdown_checkpoint",
        "CheckpointAndStopStrategy",
    ),
    "FinishCurrentToolStrategy": (
        "synthorg.engine.shutdown_finish_tool",
        "FinishCurrentToolStrategy",
    ),
    "ImmediateCancelStrategy": (
        "synthorg.engine.shutdown_immediate",
        "ImmediateCancelStrategy",
    ),
    "build_shutdown_strategy": (
        "synthorg.engine.shutdown_strategies",
        "build_shutdown_strategy",
    ),
    "StagnationConfig": ("synthorg.engine.stagnation", "StagnationConfig"),
    "StagnationDetector": ("synthorg.engine.stagnation", "StagnationDetector"),
    "StagnationResult": ("synthorg.engine.stagnation", "StagnationResult"),
    "StagnationVerdict": ("synthorg.engine.stagnation", "StagnationVerdict"),
    "ToolRepetitionDetector": (
        "synthorg.engine.stagnation",
        "ToolRepetitionDetector",
    ),
    "TaskEngine": ("synthorg.engine.task_engine", "TaskEngine"),
    "TaskEngineConfig": ("synthorg.engine.task_engine_config", "TaskEngineConfig"),
    "CancelTaskMutation": (
        "synthorg.engine.task_engine_models",
        "CancelTaskMutation",
    ),
    "CreateTaskData": ("synthorg.engine.task_engine_models", "CreateTaskData"),
    "CreateTaskMutation": (
        "synthorg.engine.task_engine_models",
        "CreateTaskMutation",
    ),
    "DeleteTaskMutation": (
        "synthorg.engine.task_engine_models",
        "DeleteTaskMutation",
    ),
    "TaskMutation": ("synthorg.engine.task_engine_models", "TaskMutation"),
    "TaskMutationResult": (
        "synthorg.engine.task_engine_models",
        "TaskMutationResult",
    ),
    "TaskStateChanged": ("synthorg.engine.task_engine_models", "TaskStateChanged"),
    "TransitionTaskMutation": (
        "synthorg.engine.task_engine_models",
        "TransitionTaskMutation",
    ),
    "UpdateTaskMutation": (
        "synthorg.engine.task_engine_models",
        "UpdateTaskMutation",
    ),
    "StatusTransition": ("synthorg.engine.task_execution", "StatusTransition"),
    "TaskExecution": ("synthorg.engine.task_execution", "TaskExecution"),
    "DefaultTokenEstimator": (
        "synthorg.engine.token_estimation",
        "DefaultTokenEstimator",
    ),
    "PromptTokenEstimator": (
        "synthorg.engine.token_estimation",
        "PromptTokenEstimator",
    ),
    "MergeConflict": ("synthorg.engine.workspace", "MergeConflict"),
    "MergeOrchestrator": ("synthorg.engine.workspace", "MergeOrchestrator"),
    "MergeResult": ("synthorg.engine.workspace", "MergeResult"),
    "PlannerWorktreesConfig": (
        "synthorg.engine.workspace",
        "PlannerWorktreesConfig",
    ),
    "PlannerWorktreeStrategy": (
        "synthorg.engine.workspace",
        "PlannerWorktreeStrategy",
    ),
    "Workspace": ("synthorg.engine.workspace", "Workspace"),
    "WorkspaceGroupResult": ("synthorg.engine.workspace", "WorkspaceGroupResult"),
    "WorkspaceIsolationConfig": (
        "synthorg.engine.workspace",
        "WorkspaceIsolationConfig",
    ),
    "WorkspaceIsolationService": (
        "synthorg.engine.workspace",
        "WorkspaceIsolationService",
    ),
    "WorkspaceIsolationStrategy": (
        "synthorg.engine.workspace",
        "WorkspaceIsolationStrategy",
    ),
    "WorkspaceRequest": ("synthorg.engine.workspace", "WorkspaceRequest"),
    "ZERO_TOKEN_USAGE": ("synthorg.providers.models", "ZERO_TOKEN_USAGE"),
    "add_token_usage": ("synthorg.providers.models", "add_token_usage"),
}

_LAZY_EXPORT_LOCK: Final[threading.Lock] = threading.Lock()


def __getattr__(name: str) -> object:
    """Resolve and cache a lazily-exported symbol on first access (PEP 562).

    Returns:
        The resolved (and now cached) export object for ``name``.

    Raises:
        AttributeError: When ``name`` is not a known lazy export.
    """
    if name not in _LAZY_EXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    import importlib  # noqa: PLC0415

    if name in globals():
        return globals()[name]
    module_path, attr = _LAZY_EXPORTS[name]
    # Resolve the import OUTSIDE the lock: importing the target runs arbitrary
    # module-level code that can re-enter this hub (the import cycles this lazy
    # machinery exists to break), so holding a non-reentrant lock across the
    # import would risk a same-thread self-deadlock or a cross-hub lock-order
    # inversion. Python's per-module import lock already dedups the work, so a
    # racing first access at worst resolves the idempotent value twice;
    # ``setdefault`` keeps a single cached object.
    value = getattr(importlib.import_module(module_path), attr)
    with _LAZY_EXPORT_LOCK:
        return globals().setdefault(name, value)


def __dir__() -> list[str]:
    """Include the lazily-exported names in ``dir()`` / autocomplete.

    Returns:
        The sorted list of public export names.
    """
    return sorted(__all__)


__all__ = [
    "DEFAULT_MAX_TURNS",
    "STRATEGY_MAP",
    "STRATEGY_NAME_AUCTION",
    "STRATEGY_NAME_COST_OPTIMIZED",
    "STRATEGY_NAME_HIERARCHICAL",
    "STRATEGY_NAME_LOAD_BALANCED",
    "STRATEGY_NAME_MANUAL",
    "STRATEGY_NAME_ROLE_BASED",
    "ZERO_TOKEN_USAGE",
    "AgentAssignment",
    "AgentContext",
    "AgentContextSnapshot",
    "AgentContribution",
    "AgentEngine",
    "AgentMiddleware",
    "AgentMiddlewareChain",
    "AgentMiddlewareContext",
    "AgentOutcome",
    "AgentRunResult",
    "AgentRuntimeState",
    "AgentTaskScorer",
    "AgentWorkload",
    "ApprovalGate",
    "AssignmentCandidate",
    "AssignmentRequest",
    "AssignmentResult",
    "AssumptionViolationEvent",
    "AssumptionViolationType",
    "AuctionBidRanker",
    "AutoTopologyConfig",
    "BaseAgentMiddleware",
    "BaseCoordinationMiddleware",
    "BudgetChecker",
    "CancelTaskMutation",
    "CandidatePoolFilter",
    "CandidateRanker",
    "Checkpoint",
    "CheckpointAndStopStrategy",
    "CheckpointCallback",
    "CheckpointConfig",
    "CheckpointRecoveryStrategy",
    "CheckpointSaver",
    "ClarificationRequiredError",
    "ClassificationResult",
    "CleanupCallback",
    "ContextDependentDispatcher",
    "CooperativeTimeoutStrategy",
    "CoordinationConfig",
    "CoordinationContext",
    "CoordinationError",
    "CoordinationMiddleware",
    "CoordinationMiddlewareChain",
    "CoordinationMiddlewareContext",
    "CoordinationPhaseError",
    "CoordinationPhaseResult",
    "CoordinationResult",
    "CoordinationResultWithAttribution",
    "CoordinationWave",
    "CostDescendingRanker",
    "CreateTaskData",
    "CreateTaskMutation",
    "DecompositionContext",
    "DecompositionCycleError",
    "DecompositionDepthError",
    "DecompositionError",
    "DecompositionPlan",
    "DecompositionResult",
    "DecompositionService",
    "DecompositionStrategy",
    "DecompositionSubtaskLimitError",
    "DefaultTokenEstimator",
    "DeleteTaskMutation",
    "DependencyGraph",
    "DispatchResult",
    "EngineError",
    "ErrorFinding",
    "ErrorSeverity",
    "EscalationInfo",
    "ExecutionLoop",
    "ExecutionResult",
    "ExecutionStateError",
    "FailAndReassignStrategy",
    "FinishCurrentToolStrategy",
    "Heartbeat",
    "HierarchicalPoolFilter",
    "IdentityPoolFilter",
    "ImmediateCancelStrategy",
    "InMemoryResourceLock",
    "LlmDecompositionConfig",
    "LlmDecompositionStrategy",
    "LoopExecutionError",
    "ManualAssignmentStrategy",
    "ManualDecompositionStrategy",
    "MaxTurnsExceededError",
    "MergeConflict",
    "MergeOrchestrator",
    "MergeResult",
    "MiddlewareConfigError",
    "MiddlewareError",
    "MiddlewareRegistryError",
    "ModelCallResult",
    "MultiAgentCoordinator",
    "NoEligibleAgentError",
    "ParallelExecutionError",
    "ParallelExecutionGroup",
    "ParallelExecutionResult",
    "ParallelExecutor",
    "ParallelProgress",
    "PlannerWorktreeStrategy",
    "PlannerWorktreesConfig",
    "PoolFilterResult",
    "ProgressCallback",
    "PromptBuildError",
    "PromptTokenEstimator",
    "RankingResult",
    "ReactLoop",
    "RecoveryResult",
    "RecoveryStrategy",
    "ResourceConflictError",
    "ResourceLock",
    "ResumePayload",
    "RoutingCandidate",
    "RoutingDecision",
    "RoutingResult",
    "SasDispatcher",
    "ScoreDescendingRanker",
    "ScoringBasedAssignmentStrategy",
    "ShutdownChecker",
    "ShutdownManager",
    "ShutdownResult",
    "ShutdownStrategy",
    "StagnationConfig",
    "StagnationDetector",
    "StagnationResult",
    "StagnationVerdict",
    "StatusRollup",
    "StatusTransition",
    "SubtaskDefinition",
    "SubtaskStatusRollup",
    "SystemPrompt",
    "TaskAssignmentError",
    "TaskAssignmentService",
    "TaskAssignmentStrategy",
    "TaskCompletionMetrics",
    "TaskEngine",
    "TaskEngineConfig",
    "TaskEngineError",
    "TaskEngineNotRunningError",
    "TaskEngineQueueFullError",
    "TaskExecution",
    "TaskInternalError",
    "TaskLedger",
    "TaskMutation",
    "TaskMutationError",
    "TaskMutationResult",
    "TaskNotFoundError",
    "TaskRoutingError",
    "TaskRoutingService",
    "TaskStateChanged",
    "TaskStructureClassifier",
    "TaskVersionConflictError",
    "TerminationReason",
    "ToolCallResult",
    "ToolRepetitionDetector",
    "TopologyDispatcher",
    "TopologySelector",
    "TransitionTaskMutation",
    "UpdateTaskMutation",
    "WaveDispatcher",
    "WorkloadAscendingRanker",
    "Workspace",
    "WorkspaceCleanupError",
    "WorkspaceError",
    "WorkspaceGroupResult",
    "WorkspaceIsolationConfig",
    "WorkspaceIsolationService",
    "WorkspaceIsolationStrategy",
    "WorkspaceLimitError",
    "WorkspaceMergeError",
    "WorkspaceRequest",
    "WorkspaceSetupError",
    "add_token_usage",
    "build_execution_waves",
    "build_shutdown_strategy",
    "build_strategy_map",
    "build_system_prompt",
    "classify_execution_errors",
    "make_checkpoint_callback",
    "select_dispatcher",
]
