"""Step-execution helpers for the Hybrid Plan + ReAct loop.

Covers plan truncation, per-step instruction-message assembly, and the
turn-budget warning. Stateless free functions only; no instance state.
The per-turn machinery both plan loops share lives in
:mod:`synthorg.engine.plan_step_turn`.
"""

from synthorg.core.execution_identity import current_execution_identity
from synthorg.engine.context import AgentContext
from synthorg.engine.hybrid_models import HybridLoopConfig
from synthorg.engine.plan_models import ExecutionPlan, PlanStep
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_HYBRID_PLAN_TRUNCATED,
    EXECUTION_HYBRID_TURN_BUDGET_WARNING,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)


def truncate_plan(
    plan: ExecutionPlan,
    max_steps: int,
) -> ExecutionPlan:
    """Truncate plan to *max_steps* if it exceeds the limit.

    The run id for the truncation log is read from the ambient
    ``current_execution_identity()`` (bound at the engine run boundary)
    rather than threaded in as a parameter.

    Args:
        plan: The execution plan to potentially truncate.
        max_steps: Maximum allowed number of steps.

    Returns:
        The original plan if within limit, otherwise a truncated copy.
    """
    if len(plan.steps) <= max_steps:
        return plan
    identity = current_execution_identity()
    logger.warning(
        EXECUTION_HYBRID_PLAN_TRUNCATED,
        execution_id=identity.execution_id if identity is not None else None,
        original_steps=len(plan.steps),
        truncated_to=max_steps,
    )
    truncated_steps = tuple(
        step.model_copy(update={"step_number": i + 1})
        for i, step in enumerate(plan.steps[:max_steps])
    )
    return plan.model_copy(update={"steps": truncated_steps})


def build_step_message(step: PlanStep) -> ChatMessage:
    """Build the instruction message for a plan step.

    Args:
        step: The plan step to build a message for.

    Returns:
        A chat message instructing the LLM to execute the step.
    """
    safe_desc = wrap_untrusted(TAG_TASK_DATA, step.description)
    safe_outcome = wrap_untrusted(TAG_TASK_DATA, step.expected_outcome)
    instruction = (
        f"Execute the following step {step.step_number}:\n"
        f"Description:\n{safe_desc}\n"
        f"Expected outcome:\n{safe_outcome}\n"
        f"{untrusted_content_directive((TAG_TASK_DATA,))} When done, respond "
        f"with a summary of what you accomplished."
    )
    return ChatMessage(
        role=MessageRole.USER,
        content=instruction,
    )


def warn_insufficient_budget(
    config: HybridLoopConfig,
    ctx: AgentContext,
) -> None:
    """Log a warning if the turn budget is likely insufficient.

    Args:
        config: Hybrid loop configuration.
        ctx: Agent context with turn budget information.
    """
    # plan(1) + steps * (turns + summary(1)) -- excludes replan overhead
    estimated_min = 1 + config.max_plan_steps * (
        config.max_turns_per_step + (1 if config.checkpoint_after_each_step else 0)
    )
    if estimated_min > ctx.max_turns:
        logger.warning(
            EXECUTION_HYBRID_TURN_BUDGET_WARNING,
            execution_id=ctx.execution_id,
            estimated_min_turns=estimated_min,
            max_turns=ctx.max_turns,
            max_plan_steps=config.max_plan_steps,
            max_turns_per_step=config.max_turns_per_step,
        )
