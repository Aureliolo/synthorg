# module-kind: adapter
"""Agent-session stakeholder plan-review panel.

Reviews a built plan by running a bounded, read-only persona session AS each
seated panellist rather than a single LLM call: the lead reads the whole plan,
raises concrete findings, and calls the terminal ``submit_plan_review`` tool
with a verdict. Every panellist's verdict is consolidated (deterministically)
into one :class:`~synthorg.core.plan_review.PlanReview` attached to the plan.

Degrades gracefully: when no eligible reviewer can be seated, or no panellist
submits a usable verdict, it returns ``None`` and the plan is parked for human
approval without a panel review (a greenlight is never blocked on the panel).
"""

from typing import override

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.plan_review import PlanReview, PlanReviewerVerdict
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_persona import render_agent_system_prompt
from synthorg.engine.context import AgentContext
from synthorg.engine.decomposition.models import DecompositionResult, SubtaskDefinition
from synthorg.engine.loop_protocol import BudgetChecker, ShutdownChecker
from synthorg.engine.pipeline.plan_review_panel_port import PlanReviewPanel
from synthorg.engine.plan_review._panel_selection import select_review_panel
from synthorg.engine.plan_review.models import PlanReviewPanelConfig
from synthorg.engine.plan_review.review_tool import SubmitPlanReviewTool, VerdictCapture
from synthorg.engine.plan_review.synthesis import synthesise_review
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.engine.react_loop import ReactLoop
from synthorg.observability import get_logger
from synthorg.observability.events.plan_review import (
    PLAN_REVIEW_PANEL_COMPLETED,
    PLAN_REVIEW_PANEL_EMPTY,
    PLAN_REVIEW_PANEL_STARTED,
    PLAN_REVIEW_REVIEWER_COMPLETED,
    PLAN_REVIEW_REVIEWER_NO_VERDICT,
    PLAN_REVIEW_REVIEWER_STARTED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)


class AgentSessionPlanReviewPanel(PlanReviewPanel):
    """Panel that reviews a plan via one bounded persona session per lead.

    Structurally satisfies the pipeline's :class:`PlanReviewPanel` port; wired
    onto the work pipeline by the startup hook so the engine spine never
    imports the provider stack directly.
    """

    __slots__ = (
        "_clock",
        "_config",
        "_cost_tracker",
        "_provider",
        "_shutdown_checker",
    )

    def __init__(
        self,
        *,
        provider: CompletionProvider,
        config: PlanReviewPanelConfig | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Initialise the agent-session plan-review panel.

        Args:
            provider: LLM completion provider driving each review session.
            config: Optional panel configuration (size, turn cap, temperature,
                cost ceiling). Uses defaults when omitted.
            cost_tracker: Optional cost tracker; when wired each review
                session's provider calls record against it under the reviewer.
            shutdown_checker: Optional callback returning ``True`` when a
                graceful shutdown is in progress; a review session halts at the
                next turn boundary when it fires.
            clock: Injectable time source (defaults to the system clock).
        """
        self._provider = provider
        self._config = config or PlanReviewPanelConfig()
        self._cost_tracker = cost_tracker
        self._shutdown_checker = shutdown_checker
        self._clock = clock if clock is not None else SystemClock()

    @override
    async def review(
        self,
        *,
        task: Task,
        plan: DecompositionResult,
        agents: tuple[AgentIdentity, ...],
        owner: AgentIdentity | None,
    ) -> PlanReview | None:
        """Review *plan* with a bounded panel, consolidating the verdicts.

        Returns:
            The consolidated :class:`PlanReview`, or ``None`` when no panellist
            could be seated or none submitted a usable verdict.
        """
        panel = select_review_panel(
            plan, agents, owner=owner, limit=self._config.panel_size
        )
        if not panel:
            return None
        logger.info(
            PLAN_REVIEW_PANEL_STARTED,
            task_id=str(task.id),
            panel_size=len(panel),
            subtask_count=len(plan.plan.subtasks),
        )
        rendered = _render_plan(task, plan)
        verdicts: list[PlanReviewerVerdict] = []
        for reviewer in panel:
            verdict = await self._run_reviewer_session(task, reviewer, rendered)
            if verdict is not None:
                verdicts.append(verdict)
        if not verdicts:
            logger.info(
                PLAN_REVIEW_PANEL_EMPTY, task_id=str(task.id), reason="no_verdicts"
            )
            return None
        review = synthesise_review(tuple(verdicts), now=self._clock.now())
        logger.info(
            PLAN_REVIEW_PANEL_COMPLETED,
            task_id=str(task.id),
            reviewer_count=len(verdicts),
            verdict=review.verdict.value,
        )
        return review

    async def _run_reviewer_session(
        self,
        task: Task,
        reviewer: AgentIdentity,
        rendered_plan: str,
    ) -> PlanReviewerVerdict | None:
        """Run one panellist's bounded review session, capturing its verdict.

        Returns:
            The panellist's :class:`PlanReviewerVerdict`, or ``None`` when the
            session ended without submitting a usable verdict.
        """
        capture = VerdictCapture()
        invoker = self._build_invoker(reviewer, capture)
        ctx = self._build_context(reviewer, task, rendered_plan)
        logger.info(
            PLAN_REVIEW_REVIEWER_STARTED,
            task_id=str(task.id),
            reviewer_id=str(reviewer.id),
            reviewer_role=reviewer.role,
        )
        loop = ReactLoop(approval_gate=None)
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=NotBlankStr(str(reviewer.id)),
            task_id=str(task.id),
            # Panellist-run review session, not a registered system prompt class.
            purpose=None,
            call_category=LLMCallCategory.SYSTEM,
        ):
            await loop.execute(
                context=ctx,
                provider=self._provider,
                tool_invoker=invoker,
                budget_checker=self._budget_checker(),
                shutdown_checker=self._shutdown_checker,
                completion_config=CompletionConfig(
                    temperature=self._config.temperature
                ),
            )
        verdict = capture.verdict
        if verdict is None:
            logger.warning(
                PLAN_REVIEW_REVIEWER_NO_VERDICT,
                task_id=str(task.id),
                reviewer_id=str(reviewer.id),
            )
            return None
        logger.info(
            PLAN_REVIEW_REVIEWER_COMPLETED,
            task_id=str(task.id),
            reviewer_id=str(reviewer.id),
            verdict=verdict.verdict.value,
            finding_count=len(verdict.findings),
        )
        return verdict

    def _build_invoker(
        self,
        reviewer: AgentIdentity,
        capture: VerdictCapture,
    ) -> ToolInvoker:
        """Assemble the review session's invoker over the submit tool only.

        The panel review reads a rendered plan and needs no live tools, so the
        session is granted only the terminal submit tool: no state is observed
        or mutated.

        Returns:
            A :class:`ToolInvoker` over the single ``submit_plan_review`` tool.
        """
        submit_tool = SubmitPlanReviewTool(
            reviewer_role=NotBlankStr(reviewer.role),
            reviewer_id=NotBlankStr(str(reviewer.id)),
            capture=capture,
        )
        return ToolInvoker(
            ToolRegistry([submit_tool]),
            permission_checker=None,
            agent_id=str(reviewer.id),
            cost_tracker=self._cost_tracker,
        )

    def _build_context(
        self,
        reviewer: AgentIdentity,
        task: Task,
        rendered_plan: str,
    ) -> AgentContext:
        """Build the reviewer-persona review context for the session.

        Returns:
            An :class:`AgentContext` carrying the reviewer persona and the
            fenced review brief.
        """
        ctx = AgentContext.from_identity(reviewer, max_turns=self._config.max_turns)
        ctx = ctx.with_message(
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=render_agent_system_prompt(reviewer),
            ),
        )
        return ctx.with_message(
            ChatMessage(
                role=MessageRole.USER,
                content=_review_brief(reviewer, task, rendered_plan),
            ),
        )

    def _budget_checker(self) -> BudgetChecker:
        """Build the per-session spend-ceiling checker.

        Returns:
            A checker that halts the loop once accumulated cost reaches the
            configured ceiling.
        """
        ceiling = self._config.cost_ceiling
        return lambda ctx: ctx.accumulated_cost.cost >= ceiling


def _review_brief(reviewer: AgentIdentity, task: Task, rendered_plan: str) -> str:
    """Compose the review instruction with the fenced objective + plan.

    The objective text originates from operator/charter input and the plan is
    model-generated from it, so both are attacker-influenced and fenced via
    ``wrap_untrusted``; the instructions sit outside the fence.

    Returns:
        The user-message brief driving the review session.
    """
    return "\n".join(
        [
            f"You are the {reviewer.role} on the review panel for this plan.",
            "Review it as a stakeholder would before the company commits to it:",
            "- Is every item owned by an accountable role?",
            "- Are the stakes calibrated (not everything critical, nothing",
            "  irreversible left as normal)?",
            "- Does every item define done with verifiable acceptance criteria?",
            "- Is real work parallelised, or is it a needless sequential chain?",
            "- Do decision items carry genuine options, and is the recommended",
            "  one sound?",
            "- From your lens specifically (technical, budget, or your domain),",
            "  what is missing or risky?",
            "Raise concrete, actionable findings; do not invent problems where",
            "there are none. Then call submit_plan_review exactly once with your",
            "verdict and findings.",
            "",
            wrap_untrusted(
                TAG_TASK_DATA,
                "\n".join([f"Objective: {task.title}", "", rendered_plan]),
            ),
        ]
    )


def _render_plan(task: Task, plan: DecompositionResult) -> str:
    """Render the plan's items as review-legible text.

    Returns:
        A plain-text rendering of every item (id, title, owner, stakes, kind,
        dependencies, acceptance criteria, and decision options) for the
        reviewer to read.
    """
    del task
    lines = [f"Plan structure: {plan.plan.task_structure.value}", "Items:"]
    for index, subtask in enumerate(plan.plan.subtasks, start=1):
        lines.extend(_render_item(index, subtask))
    return "\n".join(lines)


def _render_item(index: int, subtask: SubtaskDefinition) -> list[str]:
    """Render a single plan item to review-legible lines.

    Returns:
        The text lines describing *subtask*.
    """
    owner = subtask.required_role or "UNASSIGNED"
    lines = [
        f"{index}. [{subtask.id}] {subtask.title} ({subtask.kind.value})",
        f"   owner: {owner} | stakes: {subtask.stakes.value}"
        f" | complexity: {subtask.estimated_complexity.value}",
        f"   {subtask.description}",
    ]
    if subtask.dependencies:
        lines.append(f"   depends on: {', '.join(subtask.dependencies)}")
    if subtask.acceptance_criteria:
        lines.append(f"   done when: {'; '.join(subtask.acceptance_criteria)}")
    else:
        lines.append("   done when: (none defined)")
    for option in subtask.options:
        mark = " (recommended)" if option.recommended else ""
        lines.append(f"   option [{option.id}] {option.title}{mark}: {option.summary}")
    return lines
