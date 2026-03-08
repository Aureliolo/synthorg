"""Agent execution engine.

Re-exports the public API for the agent orchestrator, run results,
system prompt construction, runtime execution state, execution loops,
and engine errors.
"""

from ai_company.engine.agent_engine import AgentEngine
from ai_company.engine.context import (
    DEFAULT_MAX_TURNS,
    AgentContext,
    AgentContextSnapshot,
)
from ai_company.engine.decomposition import (
    DecompositionContext,
    DecompositionPlan,
    DecompositionResult,
    DecompositionService,
    DecompositionStrategy,
    DependencyGraph,
    ManualDecompositionStrategy,
    StatusRollup,
    SubtaskDefinition,
    SubtaskStatusRollup,
    TaskStructureClassifier,
)
from ai_company.engine.errors import (
    BudgetExhaustedError,
    DecompositionCycleError,
    DecompositionDepthError,
    DecompositionError,
    EngineError,
    ExecutionStateError,
    LoopExecutionError,
    MaxTurnsExceededError,
    ParallelExecutionError,
    PromptBuildError,
    ResourceConflictError,
    TaskRoutingError,
)
from ai_company.engine.loop_protocol import (
    BudgetChecker,
    ExecutionLoop,
    ExecutionResult,
    ShutdownChecker,
    TerminationReason,
    TurnRecord,
)
from ai_company.engine.metrics import TaskCompletionMetrics
from ai_company.engine.parallel import ParallelExecutor, ProgressCallback
from ai_company.engine.parallel_models import (
    AgentAssignment,
    AgentOutcome,
    ParallelExecutionGroup,
    ParallelExecutionResult,
    ParallelProgress,
)
from ai_company.engine.plan_execute_loop import PlanExecuteLoop
from ai_company.engine.plan_models import (
    ExecutionPlan,
    PlanExecuteConfig,
    PlanStep,
    StepStatus,
)
from ai_company.engine.prompt import (
    DefaultTokenEstimator,
    PromptTokenEstimator,
    SystemPrompt,
    build_system_prompt,
)
from ai_company.engine.react_loop import ReactLoop
from ai_company.engine.recovery import (
    FailAndReassignStrategy,
    RecoveryResult,
    RecoveryStrategy,
)
from ai_company.engine.resource_lock import InMemoryResourceLock, ResourceLock
from ai_company.engine.routing import (
    AgentTaskScorer,
    AutoTopologyConfig,
    RoutingCandidate,
    RoutingDecision,
    RoutingResult,
    TaskRoutingService,
    TopologySelector,
)
from ai_company.engine.run_result import AgentRunResult
from ai_company.engine.shutdown import (
    CleanupCallback,
    CooperativeTimeoutStrategy,
    ShutdownManager,
    ShutdownResult,
    ShutdownStrategy,
)
from ai_company.engine.task_execution import StatusTransition, TaskExecution
from ai_company.providers.models import ZERO_TOKEN_USAGE, add_token_usage

__all__ = [
    "DEFAULT_MAX_TURNS",
    "ZERO_TOKEN_USAGE",
    "AgentAssignment",
    "AgentContext",
    "AgentContextSnapshot",
    "AgentEngine",
    "AgentOutcome",
    "AgentRunResult",
    "AgentTaskScorer",
    "AutoTopologyConfig",
    "BudgetChecker",
    "BudgetExhaustedError",
    "CleanupCallback",
    "CooperativeTimeoutStrategy",
    "DecompositionContext",
    "DecompositionCycleError",
    "DecompositionDepthError",
    "DecompositionError",
    "DecompositionPlan",
    "DecompositionResult",
    "DecompositionService",
    "DecompositionStrategy",
    "DefaultTokenEstimator",
    "DependencyGraph",
    "EngineError",
    "ExecutionLoop",
    "ExecutionPlan",
    "ExecutionResult",
    "ExecutionStateError",
    "FailAndReassignStrategy",
    "InMemoryResourceLock",
    "LoopExecutionError",
    "ManualDecompositionStrategy",
    "MaxTurnsExceededError",
    "ParallelExecutionError",
    "ParallelExecutionGroup",
    "ParallelExecutionResult",
    "ParallelExecutor",
    "ParallelProgress",
    "PlanExecuteConfig",
    "PlanExecuteLoop",
    "PlanStep",
    "ProgressCallback",
    "PromptBuildError",
    "PromptTokenEstimator",
    "ReactLoop",
    "RecoveryResult",
    "RecoveryStrategy",
    "ResourceConflictError",
    "ResourceLock",
    "RoutingCandidate",
    "RoutingDecision",
    "RoutingResult",
    "ShutdownChecker",
    "ShutdownManager",
    "ShutdownResult",
    "ShutdownStrategy",
    "StatusRollup",
    "StatusTransition",
    "StepStatus",
    "SubtaskDefinition",
    "SubtaskStatusRollup",
    "SystemPrompt",
    "TaskCompletionMetrics",
    "TaskExecution",
    "TaskRoutingError",
    "TaskRoutingService",
    "TaskStructureClassifier",
    "TerminationReason",
    "TopologySelector",
    "TurnRecord",
    "add_token_usage",
    "build_system_prompt",
]
