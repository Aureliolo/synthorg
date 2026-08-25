# module-kind: adapter
"""Agent-session stakeholder plan-review panel.

Reviews a built plan by running a bounded, read-only persona session AS each
seated panellist rather than a single LLM call: the lead reads the whole plan,
raises concrete findings, and calls the terminal ``submit_plan_review`` tool
with a verdict. Every panellist's verdict is consolidated (deterministically)
into one :class:`~synthorg.core.plan_review.PlanReview` attached to the plan.

Degrades visibly, not silently: when no eligible reviewer can be seated, or a
seated panel submits no usable verdict, the plan is still parked for human
approval (a greenlight is never blocked on the panel) but the outcome names why
it carries no review, so the operator approves knowing the plan has zero quality
signal. The one case that is NOT a degradation is every seated reviewer failing
on its provider: that is an outage, and a plan nobody could review must not
present as a plan nobody objected to.
"""

import asyncio
from dataclasses import dataclass
from functools import partial
from typing import Final, override

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.session_budget import build_session_budget_checker
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan_review import PlanReviewerVerdict, PlanReviewOutcome
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_persona import render_agent_system_prompt
from synthorg.engine.context import AgentContext
from synthorg.engine.decomposition.models import DecompositionResult, SubtaskDefinition
from synthorg.engine.errors import PlanReviewUnavailableError
from synthorg.engine.loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TerminationReason,
)
from synthorg.engine.pipeline.plan_review_panel_port import PlanReviewPanel
from synthorg.engine.plan_review._panel_selection import select_review_panel
from synthorg.engine.plan_review.models import PlanReviewPanelConfig
from synthorg.engine.plan_review.review_tool import (
    SubmitPlanReviewTool,
    VerdictCapture,
    render_category_guidance,
)
from synthorg.engine.plan_review.synthesis import synthesise_review
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.engine.react_loop import ReactLoop
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.plan_review import (
    PLAN_REVIEW_PANEL_COMPLETED,
    PLAN_REVIEW_PANEL_EMPTY,
    PLAN_REVIEW_PANEL_STARTED,
    PLAN_REVIEW_REVIEWER_COMPLETED,
    PLAN_REVIEW_REVIEWER_NO_VERDICT,
    PLAN_REVIEW_REVIEWER_PROVIDER_ERROR,
    PLAN_REVIEW_REVIEWER_RESUBMIT,
    PLAN_REVIEW_REVIEWER_SESSION_FAILED,
    PLAN_REVIEW_REVIEWER_STARTED,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import ProviderSelector
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)

#: Reasons a plan can reach the approval gate carrying no review. Each is
#: shown to the operator, because "reviewed and unobjectionable" and "never
#: reviewed" look identical on a plan that simply has no review attached.
_NO_PANEL_SEATED: Final[str] = (
    "no eligible reviewer could be seated from the active roster, so this "
    "plan carries no stakeholder review"
)
_NO_VERDICT_SUBMITTED: Final[str] = (
    "the review panel ran but no reviewer submitted a verdict, so this plan "
    "carries no stakeholder review"
)

#: The one correction a panellist gets. A session that answered in prose has
#: not abstained: it holds exactly one tool and reviewing means calling it.
#: Recording that as an absent opinion sends the plan to its human gate with
#: no quality signal at all, and does so for every panellist at once, because
#: they all fail the same way. The planning session next door already works
#: this way: it rejects an invalid submission as a correctable error and the
#: session resubmits.
_RESUBMIT_PROMPT: Final[str] = (
    "You have not submitted a review. Prose is not a verdict: this session "
    "holds exactly one tool, submit_plan_review, and reviewing means calling "
    "it. Call it now with your verdict and any findings."
)


@dataclass(frozen=True, slots=True)
class _ReviewerResult:
    """One panellist's contribution, and whether its provider answered.

    The distinction is load-bearing: a panel where every reviewer's provider
    failed reviewed nothing, and treating that as a quiet panel would park a
    plan whose review never ran as though it had passed.
    """

    verdict: PlanReviewerVerdict | None
    provider_failed: bool

    @classmethod
    def unreachable(cls) -> _ReviewerResult:
        """The session never reached a provider answer.

        An unresolvable provider, a construction failure, or the call itself
        raising: in all three nothing was reviewed, which is different from a
        reviewer that ran and stayed quiet.

        Returns:
            A verdict-less result whose provider is blamed.
        """
        return cls(verdict=None, provider_failed=True)

    @classmethod
    def from_session(
        cls,
        verdict: PlanReviewerVerdict | None,
        termination: TerminationReason,
    ) -> _ReviewerResult:
        """Read one finished session's outcome.

        Args:
            verdict: What the panellist submitted, if anything.
            termination: How its loop ended, which is the only thing that
                separates a reviewer that had nothing to say from one whose
                provider stopped it saying anything.

        Returns:
            The panellist's contribution.
        """
        return cls(
            verdict=verdict,
            provider_failed=verdict is None and termination is TerminationReason.ERROR,
        )


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
        "_provider_selector",
        "_shutdown_checker",
    )

    def __init__(
        self,
        *,
        provider_selector: ProviderSelector,
        config: PlanReviewPanelConfig | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Initialise the agent-session plan-review panel.

        Args:
            provider_selector: Resolves the completion client for a panellist's
                own ``identity.model.provider``, so each reviewer runs on the
                provider it is bound to rather than a shared default.
            config: Optional panel configuration (size, turn cap, temperature,
                cost ceiling). Uses defaults when omitted.
            cost_tracker: Optional cost tracker; when wired each review
                session's provider calls record against it under the reviewer.
            shutdown_checker: Optional callback returning ``True`` when a
                graceful shutdown is in progress; a review session halts at the
                next turn boundary when it fires.
            clock: Injectable time source (defaults to the system clock).
        """
        self._provider_selector = provider_selector
        self._config = config or PlanReviewPanelConfig()
        self._cost_tracker = cost_tracker
        self._shutdown_checker = shutdown_checker
        self._clock = clock if clock is not None else SystemClock()

    @property
    @override
    def max_revision_rounds(self) -> int:
        """How many re-plan rounds this panel's findings may drive.

        Returns:
            The configured cap, baked in at construction like every other
            bound on a review round.
        """
        return self._config.max_revision_rounds

    @override
    async def review(
        self,
        *,
        task: Task,
        plan: DecompositionResult,
        agents: tuple[AgentIdentity, ...],
        owner: AgentIdentity | None,
    ) -> PlanReviewOutcome:
        """Review *plan* with a bounded panel, consolidating the verdicts.

        Returns:
            The outcome: a consolidated :class:`PlanReview`, or the reason
            the plan carries none.

        Raises:
            PlanReviewUnavailableError: When every seated reviewer failed on
                its provider. Nothing was reviewed, so the caller fails plan
                preparation rather than parking an unreviewed plan that reads
                like an unobjectionable one.
        """
        panel = select_review_panel(
            plan, agents, owner=owner, limit=self._config.panel_size
        )
        if not panel:
            logger.info(
                PLAN_REVIEW_PANEL_EMPTY, task_id=str(task.id), reason="no_panel_seated"
            )
            return PlanReviewOutcome(absent_reason=NotBlankStr(_NO_PANEL_SEATED))
        logger.info(
            PLAN_REVIEW_PANEL_STARTED,
            task_id=str(task.id),
            panel_size=len(panel),
            subtask_count=len(plan.plan.subtasks),
        )
        rendered = _render_plan(plan)
        # Outside the isolated session, so a missing guidance entry (a defect
        # in this module, identical for every reviewer) fails here rather than
        # degrading every panellist and being reported as a provider outage.
        guidance = render_category_guidance()
        async with asyncio.TaskGroup() as group:
            sessions = [
                group.create_task(
                    self._run_reviewer_session(task, reviewer, rendered, guidance)
                )
                for reviewer in panel
            ]
        results = [session.result() for session in sessions]
        verdicts = [r.verdict for r in results if r.verdict is not None]
        if not verdicts:
            return self._outcome_without_verdicts(task, results)
        review = synthesise_review(tuple(verdicts), now=self._clock.now())
        logger.info(
            PLAN_REVIEW_PANEL_COMPLETED,
            task_id=str(task.id),
            reviewer_count=len(verdicts),
            verdict=review.verdict.value,
        )
        return PlanReviewOutcome(review=review)

    def _outcome_without_verdicts(
        self, task: Task, results: list[_ReviewerResult]
    ) -> PlanReviewOutcome:
        """Decide what a panel that submitted no verdict at all means.

        Returns:
            The outcome carrying the reason the plan holds no review.

        Raises:
            PlanReviewUnavailableError: When every seated reviewer failed on
                its provider. Nothing was reviewed, which is a different fact
                from a panel that read the plan and had nothing to say.
        """
        if all(r.provider_failed for r in results):
            msg = (
                f"every seated reviewer ({len(results)}) failed on its "
                "provider, so the plan was not reviewed at all"
            )
            logger.error(
                PLAN_REVIEW_PANEL_EMPTY,
                task_id=str(task.id),
                reason="all_providers_failed",
                panel_size=len(results),
            )
            raise PlanReviewUnavailableError(msg)
        logger.info(PLAN_REVIEW_PANEL_EMPTY, task_id=str(task.id), reason="no_verdicts")
        return PlanReviewOutcome(absent_reason=NotBlankStr(_NO_VERDICT_SUBMITTED))

    async def _run_reviewer_session(
        self,
        task: Task,
        reviewer: AgentIdentity,
        rendered_plan: str,
        guidance: str,
    ) -> _ReviewerResult:
        """Run one panellist's bounded review session, capturing its verdict.

        Isolated so one panellist's failure never blocks the greenlight: any
        unexpected exception (critical errors excepted) degrades to "no verdict"
        rather than aborting the panel, so the fan-out under a ``TaskGroup``
        never cancels its siblings on a single bad session.

        Returns:
            The panellist's verdict, or the absence of one together with
            whether its provider was the reason.
        """
        capture = VerdictCapture()
        try:
            # Dispatch on the panellist's own bound provider, never a shared
            # default: an unregistered provider raises here and degrades this
            # panellist to "no verdict" rather than dispatching to the wrong
            # gateway (a greenlight is never blocked on the panel).
            provider = self._provider_selector(reviewer)
            invoker = self._build_invoker(reviewer, capture)
            ctx = self._build_context(reviewer, task, rendered_plan, guidance)
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
                # Panellist-run review session, not a registered system prompt.
                purpose=None,
                call_category=LLMCallCategory.SYSTEM,
            ):
                run = partial(
                    loop.execute,
                    provider=provider,
                    tool_invoker=invoker,
                    budget_checker=self._budget_checker(),
                    shutdown_checker=self._shutdown_checker,
                    completion_config=CompletionConfig(
                        temperature=self._config.temperature
                    ),
                )
                result = await run(context=ctx)
                if self._should_resubmit(capture, result):
                    logger.info(
                        PLAN_REVIEW_REVIEWER_RESUBMIT,
                        task_id=str(task.id),
                        reviewer_id=str(reviewer.id),
                        reviewer_role=reviewer.role,
                    )
                    result = await run(
                        context=result.context.with_message(
                            ChatMessage(
                                role=MessageRole.USER,
                                content=_RESUBMIT_PROMPT,
                            )
                        )
                    )
        except Exception as exc:  # noqa: BLE001 -- isolate one panellist's failure
            # lint-allow: swallow-ok -- panellist failure degrades to no-verdict
            reraise_critical(exc)
            logger.warning(
                PLAN_REVIEW_REVIEWER_SESSION_FAILED,
                task_id=str(task.id),
                reviewer_id=str(reviewer.id),
                reviewer_role=reviewer.role,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _ReviewerResult.unreachable()
        verdict = capture.verdict
        if verdict is None:
            self._log_no_verdict(task, reviewer, result.termination_reason)
        else:
            logger.info(
                PLAN_REVIEW_REVIEWER_COMPLETED,
                task_id=str(task.id),
                reviewer_id=str(reviewer.id),
                verdict=verdict.verdict.value,
                finding_count=len(verdict.findings),
            )
        return _ReviewerResult.from_session(verdict, result.termination_reason)

    @staticmethod
    def _should_resubmit(
        capture: VerdictCapture,
        result: ExecutionResult,
    ) -> bool:
        """Whether this panellist gets its one correction.

        Only a session that ran to a clean finish without using its only
        tool is correctable. A provider outage, a turn-cap hit, a budget
        stop or a shutdown all mean the session had no more to give, and
        re-running would spend another call to reach the same place.

        Returns:
            ``True`` when no verdict was captured and the session
            terminated ``COMPLETED``.
        """
        return (
            capture.verdict is None
            and result.termination_reason is TerminationReason.COMPLETED
        )

    def _log_no_verdict(
        self,
        task: Task,
        reviewer: AgentIdentity,
        termination: TerminationReason,
    ) -> None:
        """Log a verdict-less session, distinguishing a provider failure.

        A session that ended in an ``ERROR`` termination (a provider outage the
        loop absorbed) is logged distinctly from a reviewer that simply chose
        not to submit, so a systematic outage is observable rather than looking
        like a quiet panel.
        """
        if termination is TerminationReason.ERROR:
            logger.warning(
                PLAN_REVIEW_REVIEWER_PROVIDER_ERROR,
                task_id=str(task.id),
                reviewer_id=str(reviewer.id),
                reviewer_role=reviewer.role,
            )
            return
        logger.warning(
            PLAN_REVIEW_REVIEWER_NO_VERDICT,
            task_id=str(task.id),
            reviewer_id=str(reviewer.id),
        )

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
            # No permission checker and no security interceptor: with one
            # terminal submit tool and nothing else, both would be inert, and
            # so would the reviewer's binding, whose only consumer is the
            # interceptor's judge-independence comparison. Granting this
            # session a tool that acts makes all three live decisions again.
            permission_checker=None,
            agent_id=str(reviewer.id),
            cost_tracker=self._cost_tracker,
        )

    def _build_context(
        self,
        reviewer: AgentIdentity,
        task: Task,
        rendered_plan: str,
        guidance: str,
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
                content=_review_brief(reviewer, task, rendered_plan, guidance),
            ),
        )

    def _budget_checker(self) -> BudgetChecker | None:
        """Build the per-session spend-ceiling checker.

        Returns:
            A checker that halts the loop once either configured bound is
            reached, or ``None`` when neither is set.
        """
        return build_session_budget_checker(self._config.ceilings)


def _review_brief(
    reviewer: AgentIdentity,
    task: Task,
    rendered_plan: str,
    guidance: str,
) -> str:
    """Compose the review instruction with the fenced objective + plan.

    The objective text originates from operator/charter input and the plan is
    model-generated from it, so both are attacker-influenced and fenced via
    ``wrap_untrusted``; the instructions sit outside the fence.

    Args:
        reviewer: The panellist whose lens the brief is written for.
        task: The task the plan serves; its title is fenced.
        rendered_plan: The plan text, fenced.
        guidance: The rendered category vocabulary, generated once by the
            caller from the enum so the brief cannot drift from the tool
            schema, and so a missing entry fails where it is a defect
            rather than inside each panellist's isolated session.

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
            "- Is any single item carrying what should be several?",
            "- From your lens specifically (technical, budget, or your domain),",
            "  what is missing or risky?",
            "Raise concrete, actionable findings; do not invent problems where",
            "there are none. Each finding carries one of these categories:",
            guidance,
            "Then call submit_plan_review exactly once with your verdict and",
            "findings.",
            "",
            wrap_untrusted(
                TAG_TASK_DATA,
                "\n".join([f"Objective: {task.title}", "", rendered_plan]),
            ),
        ]
    )


def _render_plan(plan: DecompositionResult) -> str:
    """Render the whole plan tree as review-legible text.

    Every level, indented under the item it was split out of. A reviewer
    cannot review a plan it can only see one level of, and a recursive plan
    keeps almost all of its work below the top: rendering the top alone shows
    a handful of assemblies and none of what they assemble.

    Returns:
        A plain-text rendering of every item (id, title, owner, stakes, kind,
        dependencies, acceptance criteria, and decision options) for the
        reviewer to read.
    """
    lines = [
        f"Plan structure: {plan.plan.task_structure.value}",
        "Items:",
    ]
    lines.extend(_render_level(plan, prefix="", depth=0))
    return "\n".join(lines)


def _render_level(node: DecompositionResult, *, prefix: str, depth: int) -> list[str]:
    """Render one level of the tree, then each subtree hanging off it.

    Args:
        node: The level to render.
        prefix: What each item's number is written under, so an item reads
            ``2.3`` rather than ``3`` and a reviewer can say which subtree a
            finding is about without quoting an id.
        depth: How far to indent, which is what makes containment visible.

    Returns:
        The text lines describing this level and everything below it.
    """
    below = {child.plan.parent_task_id: child for child in node.children}
    lines: list[str] = []
    for index, subtask in enumerate(node.plan.subtasks, start=1):
        label = f"{prefix}{index}"
        child = below.get(subtask.id)
        lines.extend(
            _render_item(
                label,
                subtask,
                pad="  " * depth,
                assembles=None if child is None else len(child.plan.subtasks),
            )
        )
        if child is not None:
            lines.extend(_render_level(child, prefix=f"{label}.", depth=depth + 1))
    return lines


def _render_item(
    label: str,
    subtask: SubtaskDefinition,
    *,
    pad: str,
    assembles: int | None,
) -> list[str]:
    """Render a single plan item to review-legible lines.

    Args:
        label: The item's position in the tree, such as ``2.3``.
        subtask: The definition to render.
        pad: The indent for this level.
        assembles: How many items this one was split into, or ``None`` when
            it is work rather than an assembly of work.

    Returns:
        The text lines describing *subtask*.
    """
    owner = subtask.required_role or "UNASSIGNED"
    role = subtask.kind.value if assembles is None else f"assembles {assembles} below"
    lines = [
        f"{pad}{label}. [{subtask.id}] {subtask.title} ({role})",
        (
            f"{pad}   owner: {owner} | stakes: {subtask.stakes.value}"
            f" | complexity: {subtask.estimated_complexity.value}"
        ),
        f"{pad}   {subtask.description}",
    ]
    if subtask.dependencies:
        lines.append(f"{pad}   depends on: {', '.join(subtask.dependencies)}")
    if subtask.acceptance_criteria:
        lines.append(f"{pad}   done when: {'; '.join(subtask.acceptance_criteria)}")
    else:
        lines.append(f"{pad}   done when: (none defined)")
    if subtask.unsplit_reason is not None:
        # The machine's own evidence for an OVERSIZED_SCOPE finding, put in
        # front of the reviewer rather than left in a container log.
        lines.append(f"{pad}   still oversized: {subtask.unsplit_reason}")
    for option in subtask.options:
        mark = " (recommended)" if option.recommended else ""
        lines.append(
            f"{pad}   option [{option.id}] {option.title}{mark}: {option.summary}"
        )
    return lines
