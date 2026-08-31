# module-kind: code
"""The input bundle for one ``AgentEngine._execute`` call.

``_execute`` took thirteen parameters threaded by hand through two call
sites and a protocol declaration. Half of them are same-typed strings and
optional collaborators, so a transposition survives type checking: swapping
``agent_id`` and ``task_id`` type-checks and produces a run tagged against
the wrong entity.

Bundling them means the call sites name the object once and the signature
stops being a place where order matters.
"""

from dataclasses import dataclass

from synthorg.budget.session_budget import SessionBudgetChecker
from synthorg.core.agent import AgentIdentity
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr, require_not_blank
from synthorg.engine.context import AgentContext
from synthorg.engine.prompt import SystemPrompt
from synthorg.providers.models import CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.protocol import ToolInvokerProtocol


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentExecuteRequest:
    """Everything one execution run needs, fixed for its duration.

    Keyword-only by construction: ``agent_id`` and ``task_id`` are both
    ``str`` and adjacent, which is exactly the pair a positional constructor
    would let a caller swap silently.
    """

    identity: AgentIdentity
    task: Task
    agent_id: NotBlankStr
    task_id: NotBlankStr
    ctx: AgentContext
    system_prompt: SystemPrompt
    start: float
    completion_config: CompletionConfig | None = None
    timeout_seconds: float | None = None
    tool_invoker: ToolInvokerProtocol | None = None
    effective_autonomy: EffectiveAutonomy | None = None
    provider: CompletionProvider | None = None
    project_budget: float = 0.0
    budget_checker: SessionBudgetChecker | None = None

    def __post_init__(self) -> None:
        """Reject the identifiers a run cannot be attributed without.

        Raises:
            ValueError: If ``agent_id`` or ``task_id`` is blank.
        """
        require_not_blank(self.agent_id, "agent_id")
        require_not_blank(self.task_id, "task_id")
