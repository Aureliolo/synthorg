# module-kind: code
"""Engine-boundary firing of the agent middleware chain.

The agent middleware chain (``engine/middleware/``) exposes six async
hooks; this module fires the once-per-run ``before_agent`` and
``after_agent`` hooks at the :class:`AgentEngine` execution boundary and
applies their effects back onto the run.

The headline live effect is authority-deference defence: when
``AuthorityDeferenceGuard.before_agent`` detects authority cues in the
agent's conversation, it returns a ``justification_header`` in the
middleware context metadata; this module injects that header as a system
message so the model is reminded to defer only to legitimate authority.
The remaining default middleware are named ordering slots whose real
behaviour runs inline elsewhere in the engine, so firing them is a safe
no-op that reserves the chain's ordering contract.
"""

from synthorg.core.agent import AgentIdentity
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.engine.middleware.models import AgentMiddlewareContext
from synthorg.engine.middleware.protocol import AgentMiddlewareChain
from synthorg.observability import get_logger
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)

_AUTHORITY_METADATA_KEY = "authority_deference"


def _build_context(  # noqa: PLR0913 -- keyword-only DI
    *,
    ctx: AgentContext,
    identity: AgentIdentity,
    task: Task,
    agent_id: str,
    task_id: str,
    effective_autonomy: EffectiveAutonomy | None,
) -> AgentMiddlewareContext:
    """Build the middleware context wrapping the engine run state.

    Returns:
        The :class:`AgentMiddlewareContext` for one middleware hook pass.
    """
    return AgentMiddlewareContext(
        agent_context=ctx,
        identity=identity,
        task=task,
        agent_id=NotBlankStr(agent_id),
        task_id=NotBlankStr(task_id),
        execution_id=NotBlankStr(ctx.execution_id),
        effective_autonomy=effective_autonomy,
    )


def _inject_authority_header(
    ctx: AgentContext,
    metadata: dict[str, object],
) -> AgentContext:
    """Prepend the authority justification header when one was raised.

    Returns:
        ``ctx`` with the justification header prepended as a system
        message when ``before_agent`` flagged an injection, otherwise
        ``ctx`` unchanged.
    """
    auth = metadata.get(_AUTHORITY_METADATA_KEY)
    if not isinstance(auth, dict) or not auth.get("inject_header"):
        return ctx
    header = auth.get("justification_header")
    if not isinstance(header, str) or not header.strip():
        return ctx
    return ctx.model_copy(
        update={
            "conversation": (
                ChatMessage(role=MessageRole.SYSTEM, content=header),
                *ctx.conversation,
            )
        }
    )


async def apply_before_agent(  # noqa: PLR0913 -- keyword-only DI
    chain: AgentMiddlewareChain | None,
    *,
    ctx: AgentContext,
    identity: AgentIdentity,
    task: Task,
    agent_id: str,
    task_id: str,
    effective_autonomy: EffectiveAutonomy | None,
) -> AgentContext:
    """Fire ``before_agent`` hooks and apply their effects to *ctx*.

    A ``None`` chain (middleware disabled) returns *ctx* unchanged, so the
    engine calls this unconditionally without its own guard.

    Returns:
        The agent context after every ``before_agent`` hook has run,
        with the authority justification header injected when raised.
    """
    if chain is None:
        return ctx
    mw_ctx = _build_context(
        ctx=ctx,
        identity=identity,
        task=task,
        agent_id=agent_id,
        task_id=task_id,
        effective_autonomy=effective_autonomy,
    )
    result = await chain.run_before_agent(mw_ctx)
    return _inject_authority_header(result.agent_context, result.metadata)


async def apply_after_agent(  # noqa: PLR0913 -- keyword-only DI
    chain: AgentMiddlewareChain | None,
    *,
    ctx: AgentContext,
    identity: AgentIdentity,
    task: Task,
    agent_id: str,
    task_id: str,
    effective_autonomy: EffectiveAutonomy | None,
) -> None:
    """Fire ``after_agent`` hooks for end-of-run cleanup slots.

    A ``None`` chain (middleware disabled) is a no-op, so the engine calls
    this unconditionally without its own guard.

    The default ``after_agent`` middleware are ordering slots whose real
    behaviour runs inline in the post-execution pipeline, so this pass
    has no return value; it preserves the chain's ordering contract and
    is the seam through which future ``after_agent`` middleware activate.
    """
    if chain is None:
        return
    mw_ctx = _build_context(
        ctx=ctx,
        identity=identity,
        task=task,
        agent_id=agent_id,
        task_id=task_id,
        effective_autonomy=effective_autonomy,
    )
    await chain.run_after_agent(mw_ctx)
