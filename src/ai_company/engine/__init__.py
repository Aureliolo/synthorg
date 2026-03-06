"""Agent execution engine.

Re-exports the public API for system prompt construction,
runtime execution state, execution loops, and engine errors.
"""

from ai_company.engine.context import (
    DEFAULT_MAX_TURNS,
    AgentContext,
    AgentContextSnapshot,
)
from ai_company.engine.errors import (
    BudgetExhaustedError,
    EngineError,
    ExecutionStateError,
    LoopExecutionError,
    MaxTurnsExceededError,
    PromptBuildError,
)
from ai_company.engine.loop_protocol import (
    BudgetChecker,
    ExecutionLoop,
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from ai_company.engine.prompt import (
    DefaultTokenEstimator,
    PromptTokenEstimator,
    SystemPrompt,
    build_system_prompt,
)
from ai_company.engine.react_loop import ReactLoop
from ai_company.engine.task_execution import StatusTransition, TaskExecution
from ai_company.providers.models import ZERO_TOKEN_USAGE, add_token_usage

__all__ = [
    "DEFAULT_MAX_TURNS",
    "ZERO_TOKEN_USAGE",
    "AgentContext",
    "AgentContextSnapshot",
    "BudgetChecker",
    "BudgetExhaustedError",
    "DefaultTokenEstimator",
    "EngineError",
    "ExecutionLoop",
    "ExecutionResult",
    "ExecutionStateError",
    "LoopExecutionError",
    "MaxTurnsExceededError",
    "PromptBuildError",
    "PromptTokenEstimator",
    "ReactLoop",
    "StatusTransition",
    "SystemPrompt",
    "TaskExecution",
    "TerminationReason",
    "TurnRecord",
    "add_token_usage",
    "build_system_prompt",
]
