"""Safe-boundary steering hook for the execution loops.

``check_steering`` is consulted at a turn boundary (mirroring the stagnation
``check`` in ``loop_helpers``). It projects the active steering directives for
the running agent's project, injects each not-yet-adopted one as a USER message,
records the adoption on the (checkpointed) context, and, for a REDIRECT, records
a pending replan the Plan/Hybrid loops consume at the next step boundary.

This module owns the steering message wrap, so ``loop_helpers`` (which must not
wrap, per its module note) stays pure control flow. The directive text is stored
raw in the brain and wrapped here with ``TAG_BRAIN_STATE`` at the injection
boundary so untrusted operator text cannot break out of its fence into the
trusted prompt frame.
"""

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import InterventionKind
from synthorg.engine.context import AgentContext
from synthorg.engine.intervention.inbox import SteeringInbox
from synthorg.engine.intervention.models import ActiveSteeringDirective
from synthorg.engine.prompt_safety import TAG_BRAIN_STATE, wrap_untrusted
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import (
    STEERING_DIRECTIVE_ADOPTED,
    STEERING_INBOX_READ_FAILED,
    STEERING_REPLAN_TRIGGERED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)

_REDIRECT_INSTRUCTION = (
    "OPERATOR STEERING DIRECTIVE (REDIRECT). Adopt this constraint now: revise "
    "your plan to honour it and abandon any work it makes obsolete. It overrides "
    "earlier instructions where they conflict."
)
_HINT_INSTRUCTION = (
    "OPERATOR STEERING HINT. Consider this guidance for the rest of the task; it "
    "does not require you to replan."
)


def build_steering_message(directive: ActiveSteeringDirective) -> ChatMessage:
    """Build the USER message that injects a directive into agent context.

    The trusted instruction frames the fenced, untrusted operator text.

    Returns:
        A USER-role :class:`ChatMessage` carrying the wrapped directive.
    """
    instruction = (
        _REDIRECT_INSTRUCTION
        if directive.kind is InterventionKind.REDIRECT
        else _HINT_INSTRUCTION
    )
    fenced = wrap_untrusted(TAG_BRAIN_STATE, directive.text)
    return ChatMessage(role=MessageRole.USER, content=f"{instruction}\n\n{fenced}")


def resolve_steering_scope(
    ctx: AgentContext,
) -> tuple[str, str, str] | None:
    """Resolve ``(project_id, task_id, agent_id)`` for steering, if in scope.

    Steering is project-scoped, so a run with no bound task has no anchor and
    is out of scope. Every task carries a (non-blank) project id.

    Returns:
        The scope tuple, or ``None`` when the run is not bound to a task.
    """
    task_execution = ctx.task_execution
    if task_execution is None:
        return None
    return (
        task_execution.task.project,
        str(task_execution.task.id),
        str(ctx.identity.id),
    )


async def check_steering(
    ctx: AgentContext,
    steering_inbox: SteeringInbox | None,
    *,
    execution_id: str,
) -> AgentContext | None:
    """Adopt pending steering directives at a safe boundary.

    Best-effort: an inbox failure is logged and skipped so steering never
    interrupts an otherwise-healthy loop (``MemoryError`` / ``RecursionError``
    propagate).

    Args:
        ctx: Current agent context.
        steering_inbox: The steering inbox; ``None`` disables steering.
        execution_id: Execution identifier for structured logging.

    Returns:
        The updated context when one or more directives were injected (with
        adoption recorded and, for a REDIRECT, ``pending_steering_replan_id``
        set); ``None`` when there was nothing to adopt.
    """
    if steering_inbox is None:
        return None
    scope = resolve_steering_scope(ctx)
    if scope is None:
        return None
    project_id, task_id, agent_id = scope

    try:
        directives = await steering_inbox.pending(
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            already_adopted=ctx.adopted_steering_ids,
        )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            STEERING_INBOX_READ_FAILED,
            execution_id=execution_id,
            project_id=project_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None

    if not directives:
        return None

    updated = ctx
    for directive in directives:
        updated = updated.with_message(build_steering_message(directive))
        updated = updated.with_steering_adopted(directive.entry_id)
        logger.info(
            STEERING_DIRECTIVE_ADOPTED,
            execution_id=execution_id,
            project_id=project_id,
            task_id=task_id,
            agent_id=agent_id,
            directive_id=directive.entry_id,
            kind=directive.kind.value,
        )
        if directive.requires_replan:
            updated = updated.with_pending_replan(directive.entry_id)
            logger.info(
                STEERING_REPLAN_TRIGGERED,
                execution_id=execution_id,
                directive_id=directive.entry_id,
            )
    return updated
