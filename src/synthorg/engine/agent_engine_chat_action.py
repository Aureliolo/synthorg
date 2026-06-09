# module-kind: service
"""Chat-action mixin for :class:`AgentEngine`.

A direct chat instruction can drive a real MCP action under the acting
agent's trust level. :meth:`AgentEngineChatActionMixin.run_chat_action`
runs a short tool-capable completion loop that REUSES the engine's
governed tool invoker (``_make_tool_invoker``: trust-narrowed registry
+ ``mcp_self_consumer`` tools + ``ToolPermissionChecker`` + the SecOps
security interceptor) and its shared :class:`ApprovalGate`, but WITHOUT
the Task lifecycle, project-membership validation, or workspace/sandbox
provisioning of the task path. A sensitive action escalates and parks
exactly as the task path does (``source=PARKED_CONTEXT``); the decision
resumes via the worker's taskless branch into
:meth:`resume_parked_chat_action`.
"""

from typing import TYPE_CHECKING, Final

from synthorg.core.agent import AgentIdentity
from synthorg.engine.agent_persona import render_agent_system_prompt
from synthorg.engine.chat_action import ChatActionResult, ExecutedToolCall
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ExecutionStateError
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.engine.react_loop import ReactLoop
from synthorg.observability import get_logger
from synthorg.observability.correlation import correlation_scope
from synthorg.observability.events.execution import (
    EXECUTION_CHAT_ACTION_COMPLETED,
    EXECUTION_CHAT_ACTION_PARKED,
    EXECUTION_CHAT_ACTION_RESUME_COMPLETED,
    EXECUTION_CHAT_ACTION_RESUME_STARTED,
    EXECUTION_CHAT_ACTION_STARTED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

if TYPE_CHECKING:
    from synthorg.core.clock import Clock
    from synthorg.engine._agent_engine_callables import MakeToolInvoker
    from synthorg.engine.approval_gate import ApprovalGate
    from synthorg.providers.protocol import CompletionProvider
    from synthorg.security.autonomy.models import EffectiveAutonomy

logger = get_logger(__name__)

DEFAULT_CHAT_ACTION_MAX_TURNS: Final[int] = 6
"""Default turn cap for a chat-driven action (act -> observe -> act ...)."""

_UNKNOWN_TOOL_NAME: Final[str] = "unknown"


class AgentEngineChatActionMixin:
    """Run and resume short, governed, taskless chat-driven actions."""

    _clock: Clock
    _provider: CompletionProvider
    _approval_gate: ApprovalGate | None
    _make_tool_invoker: MakeToolInvoker

    async def run_chat_action(
        self,
        *,
        identity: AgentIdentity,
        instruction: str,
        effective_autonomy: EffectiveAutonomy | None = None,
        max_turns: int = DEFAULT_CHAT_ACTION_MAX_TURNS,
    ) -> ChatActionResult:
        """Drive a real MCP action from a chat instruction under trust.

        Builds a minimal taskless context (persona ``system`` prompt
        from the shared renderer plus the untrusted-content fenced
        instruction),
        then runs the governed ReAct loop. A permitted tool executes; a
        sensitive tool escalates and parks (``PARKED_CONTEXT``).

        Args:
            identity: The acting agent.
            instruction: The human instruction (wrapped untrusted).
            effective_autonomy: Autonomy tier governing the invoker, or
                ``None`` to leave the rule engine governing without the
                autonomy-tier layer.
            max_turns: Hard turn cap for the action loop.

        Returns:
            A :class:`ChatActionResult` reporting the executed tools and
            final message, or the parked ``approval_id`` when gated.
        """
        agent_id = str(identity.id)
        system_prompt = render_agent_system_prompt(identity)
        ctx = AgentContext.from_identity(identity, max_turns=max_turns)
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
        )
        ctx = ctx.with_message(
            ChatMessage(
                role=MessageRole.USER,
                content=wrap_untrusted(TAG_TASK_DATA, instruction),
            ),
        )
        with correlation_scope(agent_id=agent_id):
            logger.info(
                EXECUTION_CHAT_ACTION_STARTED,
                agent_id=agent_id,
                max_turns=max_turns,
            )
            result = await self._run_chat_loop(ctx, effective_autonomy)
        return self._to_chat_action_result(result, agent_id=agent_id)

    async def resume_parked_chat_action(
        self,
        *,
        parked_context: AgentContext,
        approval_id: str,
        decision_message: str,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> ChatActionResult:
        """Continue a taskless chat action after an approval decision.

        Sibling to :meth:`AgentEngine.resume_parked_run` for contexts
        that carry no ``task_execution`` (a chat action has no task).
        The restored conversation already carries the persona prompt and
        the escalated tool's notice; this injects the decision as a
        ``SYSTEM`` message and re-runs the governed loop.

        Args:
            parked_context: The deserialized taskless context.
            approval_id: The approval item identifier (audit context).
            decision_message: Decision text from
                ``ApprovalGate.build_resume_message``.
            effective_autonomy: Autonomy tier for the resumed invoker.

        Returns:
            The terminal :class:`ChatActionResult` of the resumed action.

        Raises:
            ExecutionStateError: If the parked context carries a
                ``task_execution`` (a task resume must use
                :meth:`resume_parked_run`, not this taskless path).
        """
        ctx = parked_context
        if ctx.task_execution is not None:
            msg = (
                f"Chat-action resume for approval {approval_id!r} received a "
                f"task-bound context; route task resumes via resume_parked_run"
            )
            raise ExecutionStateError(msg)
        agent_id = str(ctx.identity.id)
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.SYSTEM, content=decision_message),
        )
        with correlation_scope(agent_id=agent_id):
            logger.info(
                EXECUTION_CHAT_ACTION_RESUME_STARTED,
                approval_id=approval_id,
                agent_id=agent_id,
            )
            result = await self._run_chat_loop(ctx, effective_autonomy)
            logger.info(
                EXECUTION_CHAT_ACTION_RESUME_COMPLETED,
                approval_id=approval_id,
                agent_id=agent_id,
                termination_reason=result.termination_reason.value,
            )
        return self._to_chat_action_result(result, agent_id=agent_id)

    async def _run_chat_loop(
        self,
        ctx: AgentContext,
        effective_autonomy: EffectiveAutonomy | None,
    ) -> ExecutionResult:
        """Run the governed ReAct loop over a chat-action context.

        Builds the trust-scoped tool invoker (``task_id=None``) and a
        fresh :class:`ReactLoop` wired to the engine's SHARED approval
        gate, so a parked chat action resumes on the same gate the
        ``/approvals`` controller drives. No checkpoint / stagnation /
        compaction callbacks: a chat action is short and taskless.

        Returns:
            The loop's :class:`ExecutionResult`.
        """
        tool_invoker = self._make_tool_invoker(
            ctx.identity,
            task_id=None,
            effective_autonomy=effective_autonomy,
        )
        loop = ReactLoop(approval_gate=self._approval_gate)
        return await loop.execute(
            context=ctx,
            provider=self._provider,
            tool_invoker=tool_invoker,
        )

    def _to_chat_action_result(
        self,
        result: ExecutionResult,
        *,
        agent_id: str,
    ) -> ChatActionResult:
        """Shape an :class:`ExecutionResult` into a :class:`ChatActionResult`.

        Returns:
            The mapped result: a parked ``approval_id`` for ``PARKED``,
            otherwise the final assistant message plus executed tools.
        """
        tool_calls = self._extract_tool_calls(result.context)
        if result.termination_reason == TerminationReason.PARKED:
            raw_id = result.metadata.get("approval_id")
            approval_id = str(raw_id) if raw_id is not None else None
            logger.info(
                EXECUTION_CHAT_ACTION_PARKED,
                agent_id=agent_id,
                approval_id=approval_id,
            )
            return ChatActionResult(
                termination_reason=TerminationReason.PARKED,
                tool_calls=tool_calls,
                approval_id=approval_id,
            )
        logger.info(
            EXECUTION_CHAT_ACTION_COMPLETED,
            agent_id=agent_id,
            termination_reason=result.termination_reason.value,
            tool_call_count=len(tool_calls),
        )
        return ChatActionResult(
            termination_reason=result.termination_reason,
            final_message=self._final_assistant_text(result.context),
            tool_calls=tool_calls,
        )

    @staticmethod
    def _extract_tool_calls(context: AgentContext) -> tuple[ExecutedToolCall, ...]:
        """Pair each TOOL result in the conversation with its tool name.

        Returns:
            One :class:`ExecutedToolCall` per tool-result message, in
            conversation order.
        """
        names_by_id: dict[str, str] = {}
        for msg in context.conversation:
            for call in msg.tool_calls:
                names_by_id[call.id] = call.name
        executed: list[ExecutedToolCall] = []
        for msg in context.conversation:
            tool_result = msg.tool_result
            if tool_result is None:
                continue
            executed.append(
                ExecutedToolCall(
                    tool_name=(
                        names_by_id.get(tool_result.tool_call_id) or _UNKNOWN_TOOL_NAME
                    ),
                    is_error=tool_result.is_error,
                    result=tool_result.content,
                ),
            )
        return tuple(executed)

    @staticmethod
    def _final_assistant_text(context: AgentContext) -> str | None:
        """Return the last assistant message's text content, if any.

        Returns:
            The final assistant text, or ``None`` when the run produced
            no assistant text (e.g. it ended on a tool turn).
        """
        for msg in reversed(context.conversation):
            if msg.role == MessageRole.ASSISTANT and msg.content:
                return msg.content
        return None
