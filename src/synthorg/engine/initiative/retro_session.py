# module-kind: adapter
"""Owner-run retrospective distillation session.

At objective completion the accountable lead distils the retrospective by
running a real, bounded agent session rather than a single LLM call: the lead
reasons across turns, may recall prior retros and org playbooks with a
read-only ``search_memory`` tool, and finally calls the terminal
``submit_retrospective`` tool. This mirrors the owner-run planning session, for
the same reason: judging a finished objective and distilling reusable learnings
is a non-trivial chokepoint, not a mechanical step.

The session is best-effort: it returns the submitted draft, or ``None`` when
the loop ends without a usable submission. The caller decides what to do with
``None`` (it never fabricates a retrospective).
"""

from typing import cast, override

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_persona import render_agent_system_prompt
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import RetrospectiveError
from synthorg.engine.initiative.retro_models import (
    RetrospectiveDraft,
    args_to_retrospective,
    build_retrospective_tool,
)
from synthorg.engine.loop_protocol import BudgetChecker, ShutdownChecker
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    TAG_TOOL_RESULT,
    wrap_untrusted,
)
from synthorg.engine.react_loop import ReactLoop
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.retrospective import (
    RETRO_SESSION_COMPLETED,
    RETRO_SESSION_DUPLICATE_SUBMIT,
    RETRO_SESSION_NO_DRAFT,
    RETRO_SESSION_STARTED,
    RETRO_SESSION_SUBMIT_REJECTED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)


class RetroSessionConfig(BaseModel):
    """Configuration for the retrospective distillation session.

    Attributes:
        max_turns: Hard turn cap for the session.
        temperature: Sampling temperature for the distillation turns.
        cost_ceiling: Per-session spend ceiling (base currency); the session
            halts once accumulated cost reaches it.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_turns: int = Field(default=8, ge=1, le=50, description="Session turn cap")
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    cost_ceiling: float = Field(
        default=1.0,
        gt=0.0,
        description="Per-session spend ceiling in the base currency",
    )


class _RetroCapture:
    """Mutable holder for the draft a session submits via the terminal tool."""

    __slots__ = ("draft",)

    def __init__(self) -> None:
        self.draft: RetrospectiveDraft | None = None


class SubmitRetrospectiveTool(BaseTool):
    """Terminal tool: the session submits its retrospective through it.

    A malformed submission surfaces as a tool error so the lead can correct
    and resubmit within the same session.
    """

    def __init__(self, *, capture: _RetroCapture) -> None:
        tool_def = build_retrospective_tool()
        super().__init__(
            name=tool_def.name,
            description=tool_def.description,
            parameters_schema=tool_def.parameters_schema,
            category=ToolCategory.OTHER,
        )
        self._capture = capture

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Parse + capture the submitted retrospective, or report an error.

        Returns:
            A success result, or an error result describing why the draft was
            rejected so the lead retries.
        """
        try:
            draft = args_to_retrospective(cast("dict[str, JsonValue]", arguments))
        except RetrospectiveError as exc:
            logger.debug(
                RETRO_SESSION_SUBMIT_REJECTED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    f"Retrospective rejected: {safe_error_description(exc)}. "
                    "Fix the issue and call submit_retrospective again."
                ),
                is_error=True,
            )
        if self._capture.draft is not None:
            logger.warning(
                RETRO_SESSION_DUPLICATE_SUBMIT,
                previous_org_count=len(self._capture.draft.org_learnings),
                new_org_count=len(draft.org_learnings),
            )
        self._capture.draft = draft
        return ToolExecutionResult(
            content=(
                f"Retrospective accepted with {len(draft.org_learnings)} org "
                f"learnings and {len(draft.agent_learnings)} agent learnings. "
                "You may stop now."
            ),
        )


class RetroDistiller:
    """Runs the bounded owner-run session that distils a retrospective.

    Args:
        config: Session configuration (turn cap, temperature, cost ceiling).
        cost_tracker: Optional cost tracker; when wired the session's provider
            calls record against it under the lead.
        shutdown_checker: Optional callback returning ``True`` when a graceful
            shutdown is in progress; the loop halts at the next turn boundary.
    """

    __slots__ = ("_config", "_cost_tracker", "_shutdown_checker")

    def __init__(
        self,
        *,
        config: RetroSessionConfig | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
        shutdown_checker: ShutdownChecker | None = None,
    ) -> None:
        self._config = config or RetroSessionConfig()
        self._cost_tracker = cost_tracker
        self._shutdown_checker = shutdown_checker

    async def distil(
        self,
        *,
        lead: AgentIdentity,
        provider: CompletionProvider,
        brief: str,
        recall_tool: BaseTool | None = None,
    ) -> RetrospectiveDraft | None:
        """Run the session as *lead*, returning the submitted draft or ``None``.

        Args:
            lead: The accountable lead running the session.
            provider: The completion client for the session's provider.
            brief: The distillation instruction, carrying the fenced material.
            recall_tool: Optional read-only memory-recall tool granted to the
                session so the lead can check prior retros.

        Returns:
            The submitted :class:`RetrospectiveDraft`, or ``None`` when the
            session ended without a usable submission.
        """
        capture = _RetroCapture()
        tools: list[BaseTool] = [SubmitRetrospectiveTool(capture=capture)]
        if recall_tool is not None:
            tools.append(recall_tool)
        invoker = ToolInvoker(
            ToolRegistry(tools),
            permission_checker=None,
            agent_id=str(lead.id),
            cost_tracker=self._cost_tracker,
        )
        ctx = self._build_context(lead, brief)
        logger.info(
            RETRO_SESSION_STARTED,
            lead_id=str(lead.id),
            granted_tools=len(tools),
            max_turns=self._config.max_turns,
        )
        loop = ReactLoop(approval_gate=None)
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=NotBlankStr(str(lead.id)),
            task_id=f"retro:{lead.id}",
            # Owner-run session, not a registered system prompt class.
            purpose=None,
            call_category=LLMCallCategory.SYSTEM,
        ):
            result = await loop.execute(
                context=ctx,
                provider=provider,
                tool_invoker=invoker,
                budget_checker=self._budget_checker(),
                shutdown_checker=self._shutdown_checker,
                completion_config=CompletionConfig(
                    temperature=self._config.temperature
                ),
            )
        if capture.draft is not None:
            logger.info(
                RETRO_SESSION_COMPLETED,
                lead_id=str(lead.id),
                org_learnings=len(capture.draft.org_learnings),
                agent_learnings=len(capture.draft.agent_learnings),
                termination=result.termination_reason.value,
            )
            return capture.draft
        logger.warning(
            RETRO_SESSION_NO_DRAFT,
            lead_id=str(lead.id),
            termination=result.termination_reason.value,
            termination_detail=result.error_message,
        )
        return None

    def _build_context(self, lead: AgentIdentity, brief: str) -> AgentContext:
        """Build the lead-persona session context.

        The directive declares ``<tool-result>`` because the session is
        tool-capable: recalled memories arrive in later turns under that
        fence, and a memory written by an earlier agent is exactly the kind
        of content that must read as data rather than instruction.

        Returns:
            An :class:`AgentContext` carrying the lead persona and the fenced
            distillation brief.
        """
        ctx = AgentContext.from_identity(lead, max_turns=self._config.max_turns)
        ctx = ctx.with_message(
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=render_agent_system_prompt(
                    lead,
                    fences=(TAG_TASK_DATA, TAG_TOOL_RESULT),
                ),
            ),
        )
        return ctx.with_message(
            ChatMessage(role=MessageRole.USER, content=brief),
        )

    def _budget_checker(self) -> BudgetChecker:
        """Build the per-session spend-ceiling checker.

        Returns:
            A checker that halts the loop once accumulated cost reaches the
            configured ceiling.
        """
        ceiling = self._config.cost_ceiling
        return lambda ctx: ctx.accumulated_cost.cost >= ceiling


def build_retro_brief(*, material: str) -> str:
    """Compose the distillation instruction with the fenced objective material.

    The material is assembled from operator/charter input and finished work,
    including the objective title (which denormalises an attacker-controllable
    task title), so the whole of it is fenced via ``wrap_untrusted`` to keep an
    injected instruction in the title out of the lead's own turn; only the
    static instructions sit outside the fence.

    Returns:
        The user-message brief driving the distillation session.
    """
    return "\n".join(
        [
            "You are the accountable lead writing the retrospective for an",
            "objective your team has just completed. First recall prior retros",
            "and org playbooks with search_memory so you build on what the",
            "organisation already learned and do not restate it.",
            "Then distil, from the finished work below:",
            "- org_learnings: reusable lessons the WHOLE organisation should",
            "  carry forward, phrased as standing guidance ('next time X, do",
            "  Y'), each a procedure or a convention. Omit anything an agent",
            "  could rediscover by reading the codebase.",
            "- agent_learnings: what each contributor should personally",
            "  remember next time, keyed by their agent id.",
            "Keep it honest and specific; an empty list is better than filler.",
            "Finally, call submit_retrospective exactly once.",
            "",
            wrap_untrusted(TAG_TASK_DATA, material),
        ]
    )
