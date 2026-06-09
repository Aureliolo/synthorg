"""Call-signature protocols for cross-mixin ``AgentEngine`` methods.

``AgentEngine`` is assembled from several mixins that each call methods
defined on a *sibling* mixin (for example, the post-execution mixin invokes
``_apply_recovery`` from the recovery mixin). A type checker inspecting one
mixin in isolation cannot see those sibling methods, so each mixin declares
the borrowed method as a class attribute typed by one of the protocols below.

This module is imported only under ``if TYPE_CHECKING:``; it never executes at
runtime, so its collaborator imports carry no import-time cost or cycle risk.
The protocols mirror the real method signatures (keyword-only arguments and
defaults included) so the declarations stay precise rather than ``Any``.
"""

from typing import Protocol

from synthorg.budget.errors import BudgetExhaustedError
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionLoop, ExecutionResult
from synthorg.engine.prompt import SystemPrompt
from synthorg.engine.recovery import RecoveryResult
from synthorg.engine.run_result import AgentRunResult
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.security.autonomy.models import EffectiveAutonomy
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.protocol import ToolInvokerProtocol


class ApplyRecovery(Protocol):
    """Signature of ``AgentEngineRecoveryMixin._apply_recovery``."""

    async def __call__(  # noqa: PLR0913
        self,
        execution_result: ExecutionResult,
        identity: AgentIdentity,
        agent_id: str,
        task_id: str,
        *,
        completion_config: CompletionConfig | None = ...,
        effective_autonomy: EffectiveAutonomy | None = ...,
        provider: CompletionProvider | None = ...,
        project_id: str | None = ...,
    ) -> tuple[ExecutionResult, RecoveryResult | None]: ...


class ValidateProject(Protocol):
    """Signature of ``AgentEngineContextMixin._validate_project``."""

    async def __call__(
        self,
        *,
        task: Task,
        agent_id: str,
        task_id: str,
    ) -> float: ...


class ResolveLoop(Protocol):
    """Signature of ``AgentEngineFactoriesMixin._resolve_loop``."""

    async def __call__(
        self,
        task: Task,
        agent_id: str = ...,
        task_id: str = ...,
    ) -> ExecutionLoop: ...


class MakeLoopWithCallback(Protocol):
    """Signature of ``AgentEnginePostExecMixin._make_loop_with_callback``."""

    def __call__(
        self,
        loop: ExecutionLoop,
        agent_id: str,
        task_id: str,
    ) -> ExecutionLoop: ...


class MakeToolInvoker(Protocol):
    """Signature of ``AgentEngineFactoriesMixin._make_tool_invoker``."""

    def __call__(
        self,
        identity: AgentIdentity,
        task_id: str | None = ...,
        effective_autonomy: EffectiveAutonomy | None = ...,
        project_id: str | None = ...,
    ) -> ToolInvoker | None: ...


class Execute(Protocol):
    """Signature of ``AgentEngine._execute``."""

    async def __call__(  # noqa: PLR0913
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        completion_config: CompletionConfig | None,
        ctx: AgentContext,
        system_prompt: SystemPrompt,
        start: float,
        timeout_seconds: float | None = ...,
        tool_invoker: ToolInvokerProtocol | None = ...,
        effective_autonomy: EffectiveAutonomy | None = ...,
        provider: CompletionProvider | None = ...,
        project_budget: float = ...,
    ) -> AgentRunResult: ...


class HandleBudgetError(Protocol):
    """Signature of ``AgentEngineErrorsMixin._handle_budget_error``."""

    async def __call__(  # noqa: PLR0913
        self,
        *,
        exc: BudgetExhaustedError,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        duration_seconds: float,
        ctx: AgentContext | None = ...,
        system_prompt: SystemPrompt | None = ...,
    ) -> AgentRunResult: ...


class HandleFatalError(Protocol):
    """Signature of ``AgentEngineErrorsMixin._handle_fatal_error``."""

    async def __call__(  # noqa: PLR0913
        self,
        *,
        exc: Exception,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        duration_seconds: float,
        ctx: AgentContext | None = ...,
        system_prompt: SystemPrompt | None = ...,
        completion_config: CompletionConfig | None = ...,
        effective_autonomy: EffectiveAutonomy | None = ...,
        provider: CompletionProvider | None = ...,
    ) -> AgentRunResult: ...
