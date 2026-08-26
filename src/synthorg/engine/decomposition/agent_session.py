# module-kind: adapter
"""Agent-session task decomposition strategy.

Plans an objective by running a real, bounded agent session AS the staffed
owner rather than a single LLM call: the owner reasons across turns, may call
read/research tools when granted (memory recall, project brain, web search if
configured), self-critiques, and finally calls the terminal
``submit_decomposition_plan`` tool. The submitted plan carries per-subtask
``expected_artifacts`` + ``acceptance_criteria``, so the durable plan arms the
fail-loud zero-artifact guard downstream.

This is the default decomposer. It degrades gracefully: with no staffed owner,
or if the session ends without submitting a usable plan, it falls back to the
single-shot :class:`LlmDecompositionStrategy` so a greenlight is never blocked.
"""

from typing import Final, assert_never, override

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.session_budget import (
    SessionCeilings,
    build_session_budget_checker,
)
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_persona import render_agent_system_prompt
from synthorg.engine.context import AgentContext
from synthorg.engine.decomposition._session_exhaustion import raise_session_exhaustion
from synthorg.engine.decomposition.agent_session_brief import (
    PLANNING_SESSION_FENCES,
    planning_brief,
)
from synthorg.engine.decomposition.agent_session_submit import (
    PlanCapture,
    SubmitDecompositionPlanTool,
)
from synthorg.engine.decomposition.context import (
    DecompositionContext,
    depth_budget,
    width_budget,
)
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.decomposition.tool_provider import DecompositionToolProvider
from synthorg.engine.errors import (
    DecompositionDepthError,
    DecompositionSubtaskLimitError,
    DecompositionUnsplittableError,
)
from synthorg.engine.loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
    TerminationReason,
)
from synthorg.engine.react_loop import ReactLoop
from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.memory.recall_request import MemoryRecallRequest
from synthorg.observability import (
    get_logger,
    safe_error_description,
    scrub_secret_tokens,
)
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_SESSION_COMPLETED,
    DECOMPOSITION_SESSION_FALLBACK,
    DECOMPOSITION_SESSION_NO_PLAN,
    DECOMPOSITION_SESSION_RESUMED,
    DECOMPOSITION_SESSION_STARTED,
    DECOMPOSITION_SESSION_TOOL_DROPPED,
    DECOMPOSITION_VALIDATION_ERROR,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider, ProviderSelector
from synthorg.security.autonomy.enums import ActionType
from synthorg.tools.base import BaseTool
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)

_STRATEGY_NAME = "agent-session"


def _ran_without_submitting(reason: TerminationReason) -> bool:
    """Report whether a verdict-less session had a researched plan to lose.

    Substituting a single-shot plan for a session that ran on its own terms
    hands the operator a different plan than the one they asked for,
    indistinguishable from the real thing at the approval gate. Where the
    session could not run at all, nothing was lost and the fallback stands.

    A ``match`` with :func:`assert_never` rather than a membership set: the
    fallback is a safety decision per termination, so a newly-added
    :class:`TerminationReason` must be classified deliberately, and this
    makes omitting it a type error rather than a silent grant of the
    fallback.

    Returns:
        ``True`` when the session ran and produced nothing.
    """
    match reason:
        case (
            TerminationReason.COMPLETED
            | TerminationReason.NO_OP
            | TerminationReason.MAX_TURNS
            | TerminationReason.BUDGET_EXHAUSTED
            | TerminationReason.STAGNATION
        ):
            return True
        # ERROR never reached the model, SHUTDOWN lost the process under it,
        # and PARKED / CANCELLED stopped the session by a decision taken
        # outside it (an approval wait, an operator abort): in all four the
        # session was prevented from producing rather than producing nothing.
        case (
            TerminationReason.ERROR
            | TerminationReason.SHUTDOWN
            | TerminationReason.PARKED
            | TerminationReason.CANCELLED
        ):
            return False
        case _ as unreachable:
            assert_never(unreachable)


def _stopped_short(reason: TerminationReason) -> bool:
    """Report whether the session ended its own turn with its work undone.

    A planning session has exactly one deliverable, and the tools it delivers
    through hand a rejection straight back, so an agent that ends its turn
    holding a rejected plan is in the ordinary state of any coding loop: told
    what is wrong, with turns left to fix it. Ending the session there is what
    turned a punctuation rejection into a dead run; the answer is the same one
    a coding agent gets, which is to be told it has not delivered and to carry
    on.

    Separate from :func:`_ran_without_submitting`, which answers a different
    question (was there a researched plan to lose), and the same ``match`` with
    :func:`assert_never` for the same reason: continuing to spend an agent's
    turns is a decision per termination, so a new :class:`TerminationReason`
    must be classified deliberately.

    Returns:
        ``True`` when the session stopped on its own while it could still
        deliver.
    """
    match reason:
        case TerminationReason.COMPLETED | TerminationReason.NO_OP:
            return True
        # MAX_TURNS and BUDGET_EXHAUSTED are the bounds themselves, so another
        # turn is exactly what they refuse; STAGNATION means the loop is
        # already repeating itself, and re-prompting is one more repetition.
        # ERROR is the loop giving up rather than the agent doing so, whether
        # the provider failed or the corrections for unusable turns ran out;
        # either way the next turn fails the same way. SHUTDOWN, PARKED and
        # CANCELLED stopped the session from outside it, and nothing here can
        # hand it back.
        case (
            TerminationReason.MAX_TURNS
            | TerminationReason.BUDGET_EXHAUSTED
            | TerminationReason.STAGNATION
            | TerminationReason.ERROR
            | TerminationReason.SHUTDOWN
            | TerminationReason.PARKED
            | TerminationReason.CANCELLED
        ):
            return False
        case _ as unreachable:
            assert_never(unreachable)


#: Handed to a session that stopped without submitting. It names the one
#: deliverable and points at the tool results already in the conversation,
#: rather than restating a rejection the agent can read for itself.
_UNSUBMITTED_NUDGE: Final[str] = (
    "You have not submitted a plan, so this decomposition has produced "
    "nothing. Prose is not a plan. If a previous submission came back "
    "rejected, the tool result says exactly what to fix. Fix it and call "
    "submit_decomposition_plan again."
)


# A planning session may only be granted tools that observe state, never ones
# that mutate it: the objective text is attacker-controllable, so an
# LLM-driven write tool would execute ungated. Any provided tool whose action
# type is outside this read-only allowlist is dropped before the session runs.
_READ_ONLY_ACTION_TYPES: Final[frozenset[ActionType]] = frozenset(
    {
        ActionType.CODE_READ,
        ActionType.VCS_READ,
        ActionType.DB_QUERY,
        ActionType.MEMORY_READ,
        ActionType.EXTERNAL_DATA_REQUEST,
        ActionType.RESEARCH_RUN,
        ActionType.BROWSER_NAVIGATE,
    }
)


#: Fallback for a config built without resolved settings; the operator-facing
#: default lives on ``coordination.decomposition_agent_cost_ceiling``.
_DEFAULT_CEILINGS: Final[SessionCeilings] = SessionCeilings(
    cost_ceiling=2.0, token_ceiling=0
)


class AgentSessionDecompositionConfig(BaseModel):
    """Configuration for the agent-session decomposition strategy.

    Attributes:
        max_turns: Hard turn cap for the planning session.
        temperature: Sampling temperature for the planning turns.
        ceilings: Both spend bounds on the planning session. One field, not
            two, so a wiring path that resolves the money bound cannot leave
            the token bound at its default in silence: money measures nothing
            against a provider that bills by flat subscription, where cost
            never rises.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_turns: int = Field(default=12, ge=1, le=50, description="Planning turn cap")
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    ceilings: SessionCeilings = Field(
        default=_DEFAULT_CEILINGS,
        description="Per-session money + token bounds",
    )
    memory_digest_budget: int = Field(
        default=1000,
        ge=0,
        description="Token cap for the org/retro memory digest injected into "
        "the planning brief; 0 injects nothing (the tool grant still applies)",
    )


class AgentSessionDecompositionStrategy(DecompositionStrategy):
    """Decomposition strategy that plans via a bounded owner-run agent session.

    Runs a read-only, submit-terminated planning loop as the staffed owner,
    then returns the plan the owner submitted through the terminal tool. Only
    read/research tools are granted (write tools are dropped before the loop
    runs), so the session observes state but never mutates it.
    """

    __slots__ = (
        "_config",
        "_cost_tracker",
        "_fallback",
        "_planning_memory",
        "_provider_selector",
        "_shutdown_checker",
        "_tool_provider",
    )

    def __init__(
        self,
        *,
        provider_selector: ProviderSelector,
        fallback: DecompositionStrategy,
        tool_provider: DecompositionToolProvider | None = None,
        config: AgentSessionDecompositionConfig | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        planning_memory: MemoryInjectionStrategy | None = None,
    ) -> None:
        """Initialise the agent-session decomposition strategy.

        Args:
            provider_selector: Resolves the completion client for the owner's
                own ``identity.model.provider``, so the planning session runs on
                the provider the owner is bound to rather than a shared default.
            fallback: Single-shot strategy used when no owner is staffed, the
                owner's provider is unresolvable, or the session submits no
                usable plan (a greenlight is never blocked on the session).
            tool_provider: Optional builder of the owner's read/research
                planning tools; ``None`` runs the session with only the
                terminal submit tool. Any non-read-only tool it returns is
                dropped before the session runs.
            config: Optional session configuration (turn cap, temperature,
                cost ceiling). Uses defaults when omitted.
            cost_tracker: Optional cost tracker; when wired the session's
                provider calls record against it under the owner + task.
            shutdown_checker: Optional callback returning ``True`` when a
                graceful shutdown is in progress; the planning loop halts at
                the next turn boundary when it fires.
            planning_memory: Optional injection strategy that pre-seeds a
                compact org-playbook / past-retro digest into the planning
                brief, so the plan carries prior learnings even if the owner
                never calls the recall tool. ``None`` skips the digest.
        """
        self._provider_selector = provider_selector
        self._fallback = fallback
        self._tool_provider = tool_provider
        self._config = config or AgentSessionDecompositionConfig()
        self._cost_tracker = cost_tracker
        self._shutdown_checker = shutdown_checker
        self._planning_memory = planning_memory

    @override
    def get_strategy_name(self) -> str:
        """Return the strategy name."""
        return _STRATEGY_NAME

    @override
    def plans_any_task(self) -> bool:
        """Whether this strategy can plan a task it was not constructed around.

        A session over the task it is given can plan anything, but three paths
        degrade to the single-shot fallback (no owner staffed, the owner's
        provider unresolved, the session producing no plan), and the fallback
        answers for itself. Claiming unconditionally would let recursion open a
        child level that a fixed-plan fallback then rejects, because the plan it
        holds is scoped to a different parent.

        Returns:
            Whether the fallback could also plan an arbitrary task.
        """
        return self._fallback.plans_any_task()

    @override
    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        """Plan the task via an owner-run agent session, or fall back.

        The fallback covers only the cases where there was no researched
        plan to lose: no owner staffed, an unresolvable provider, or a
        session prevented from producing at all (``ERROR`` never reached
        the model, ``SHUTDOWN`` lost the process under it, ``PARKED`` and
        ``CANCELLED`` stopped it on a decision taken outside it, and
        nothing resumes a parked planning session back into this path). A
        session that ran on its own terms and finished without submitting
        is not one of them, and neither is a plan that came back too big.

        Returns:
            The decomposition plan the owner submitted, or the fallback
            strategy's plan when no session could run.

        Raises:
            DecompositionDepthError: If the current depth meets or exceeds the
                configured max depth.
            DecompositionSubtaskLimitError: If the submitted plan carries more
                subtasks than the caller allowed.
            DecompositionError: If a session ran to completion without
                submitting a plan, or if both the session and the fallback
                fail.
        """
        self._check_depth(context)
        owner = context.owner_identity
        if owner is None:
            logger.info(
                DECOMPOSITION_SESSION_FALLBACK,
                task_id=str(task.id),
                reason="no_owner_staffed",
            )
            return await self._fallback_plan(task, context)

        try:
            provider = self._provider_selector(owner)
        except DriverNotRegisteredError as exc:
            # The owner is pinned to a provider the registry does not know;
            # fall back rather than dispatch to a default gateway. Only this
            # expected miss degrades to the single-shot decomposer -- any other
            # selector failure (a programming, state, or wiring bug) propagates.
            logger.warning(
                DECOMPOSITION_SESSION_FALLBACK,
                task_id=str(task.id),
                owner_id=str(owner.id),
                reason="owner_provider_unresolved",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return await self._fallback_plan(task, context)

        capture = PlanCapture(NotBlankStr(str(task.id)))
        result = await self._run_session(task, context, owner, provider, capture)
        plan = capture.plan
        if plan is None:
            detail = (
                scrub_secret_tokens(result.error_message)
                if result.error_message is not None
                else None
            )
            logger.warning(
                DECOMPOSITION_SESSION_NO_PLAN,
                task_id=str(task.id),
                owner_id=str(owner.id),
                termination=result.termination_reason.value,
                termination_detail=detail,
            )
            self._reject_empty_session(
                task,
                owner_id=str(owner.id),
                result=result,
                detail=detail,
                declined_to_split=capture.declined_to_split,
            )
            return await self._fallback_plan(task, context)

        if len(plan.subtasks) > width_budget(context):
            # The owner researched this plan across turns with read-only
            # tools; the single-shot fallback would produce a thinner one the
            # operator never sees. Refusing surfaces the real plan's size on
            # the durable Plan as a failure reason instead, the same as every
            # other strategy does.
            over_limit = DecompositionSubtaskLimitError(
                produced=len(plan.subtasks), limit=width_budget(context)
            )
            logger.warning(
                DECOMPOSITION_VALIDATION_ERROR,
                task_id=str(task.id),
                owner_id=str(owner.id),
                subtask_count=over_limit.produced,
                max_subtasks=over_limit.limit,
                error=safe_error_description(over_limit),
            )
            raise over_limit

        logger.info(
            DECOMPOSITION_SESSION_COMPLETED,
            task_id=str(task.id),
            owner_id=str(owner.id),
            subtask_count=len(plan.subtasks),
            termination=result.termination_reason.value,
        )
        # Blank, because that is what this field MEANS: it marks a
        # substitution, so `_fallback_plan` is its only writer. A name here too
        # would put one on every plan, and a reader could then tell a
        # researched plan from a single-shot one only by knowing which strategy
        # was configured, which is exactly what the approval gate and the
        # dashboard have no way to know.
        return plan

    def _reject_empty_session(
        self,
        task: Task,
        *,
        owner_id: str,
        result: ExecutionResult,
        detail: str | None,
        declined_to_split: bool,
    ) -> None:
        """Refuse to substitute a blind plan for a session that just ran.

        A session that spent its budget without ever calling its one tool
        produced nothing, which is the planning counterpart of the
        zero-artifact guard. Falling back there replaces a researched plan
        with a single-shot one nobody asked for, and the operator approves
        the substitute believing it is the original.

        Reached only once the session has nothing left to try: an agent that
        stops while it can still deliver is told so and continues
        (:func:`_stopped_short`), so this is the exhausted case, not the
        discouraged one.

        Args:
            task: The objective the session was planning.
            owner_id: Who ran it, for the log line.
            result: How the session ended.
            detail: What its last error said, already scrubbed.
            declined_to_split: Whether its last refusal was the size
                correction, which is the one condition the level that asked
                for this one can act on.

        Raises:
            DecompositionUnsplittableError: When the session ran out of turns
                still unable to widen a level with no depth below it.
            DecompositionError: When the session produced no plan for any
                other reason, typed by which bound it reached, if any
                (see :mod:`._session_exhaustion`).
        """
        if not _ran_without_submitting(result.termination_reason):
            return
        msg = (
            f"Planning session for task {task.id} terminated "
            f"{result.termination_reason.value!r} without submitting a plan"
        )
        if detail is not None:
            msg = f"{msg}: {detail}"
        logger.warning(
            DECOMPOSITION_VALIDATION_ERROR,
            task_id=str(task.id),
            owner_id=owner_id,
            termination=result.termination_reason.value,
            error=msg,
        )
        if declined_to_split:
            raise DecompositionUnsplittableError(msg)
        raise_session_exhaustion(result.termination_reason, msg)

    async def _fallback_plan(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        """Run the single-shot fallback, stamping what produced the plan.

        Returns:
            The fallback's plan, carrying its own strategy name so the
            substitution is visible on the durable plan and at the
            approval gate rather than being indistinguishable from a
            researched one.
        """
        plan = await self._fallback.decompose(task, context)
        return plan.model_copy(
            update={"planning_strategy": self._fallback.get_strategy_name()}
        )

    async def _run_session(
        self,
        task: Task,
        context: DecompositionContext,
        owner: AgentIdentity,
        provider: CompletionProvider,
        capture: PlanCapture,
    ) -> ExecutionResult:
        """Run the planning session as *owner* until it delivers or is spent.

        The session runs in segments over ONE context, so the turn budget, the
        conversation and every rejection already in it carry across: a segment
        that ends without a submitted plan while turns remain is told so and
        continues, exactly as a coding loop that has just been handed a failing
        check does. The bounds keep their meaning, because the only terminations
        that re-enter are the ones the agent chose (:func:`_stopped_short`), and
        reaching either of those costs a turn: the loop records the turn before
        it can decide the response completed the run.

        The one segment that can record no turn is one entered with the spend
        ceiling already reached, which returns ``BUDGET_EXHAUSTED`` from its
        first budget check. That is not resumable, so it ends the loop rather
        than spinning in it.

        Args:
            task: The task being decomposed.
            context: The decomposition context (depth, owner, limits).
            owner: The staffed owner running the planning session.
            provider: The completion client for the owner's bound provider.
            capture: Sink the terminal submit tool writes the plan into.

        Returns:
            The last segment's execution result (termination reason + error
            detail for observability).
        """
        invoker, granted = self._build_invoker(task, owner, capture, context)
        ctx = await self._build_context(task, context, owner, granted)
        logger.info(
            DECOMPOSITION_SESSION_STARTED,
            task_id=str(task.id),
            owner_id=str(owner.id),
            granted_tools=len(granted),
            max_turns=self._config.max_turns,
        )
        result = await self._run_segment(task, owner, ctx, invoker, provider)
        resumes = 0
        while capture.plan is None:
            resumed = self._resume_unsubmitted(task, owner, result, resumes)
            if resumed is None:
                return result
            resumes += 1
            result = await self._run_segment(task, owner, resumed, invoker, provider)
        return result

    def _resume_unsubmitted(
        self,
        task: Task,
        owner: AgentIdentity,
        result: ExecutionResult,
        resumes: int,
    ) -> AgentContext | None:
        """Extend the session's context with the nudge, when one is warranted.

        Args:
            task: The task being decomposed.
            owner: The staffed owner running the planning session.
            result: The segment that ended without a submitted plan.
            resumes: How many times this session has already been told.

        Returns:
            The context to run the next segment with, or ``None`` when the
            session stopped for a reason another turn cannot answer, or has no
            turn left to answer it in.
        """
        if not _stopped_short(result.termination_reason):
            return None
        if not result.context.has_turns_remaining:
            return None
        logger.info(
            DECOMPOSITION_SESSION_RESUMED,
            task_id=str(task.id),
            owner_id=str(owner.id),
            # The count is what separates a session converging on a plan from
            # one spending its budget being handed the same rejection; without
            # it the two read identically, one line at a time.
            resume_count=resumes + 1,
            turns_used=result.context.turn_count,
            max_turns=result.context.max_turns,
        )
        return result.context.with_message(
            ChatMessage(role=MessageRole.USER, content=_UNSUBMITTED_NUDGE)
        )

    async def _run_segment(
        self,
        task: Task,
        owner: AgentIdentity,
        ctx: AgentContext,
        invoker: ToolInvoker,
        provider: CompletionProvider,
    ) -> ExecutionResult:
        """Run one bounded stretch of the planning loop over *ctx*.

        Args:
            task: The task being decomposed.
            owner: The staffed owner running the planning session.
            ctx: The session context, carrying every turn spent so far.
            invoker: The session's tool invoker (submit plus read tools).
            provider: The completion client for the owner's bound provider.

        Returns:
            The loop's execution result for this stretch.
        """
        loop = ReactLoop(approval_gate=None)
        async with cost_recording_scope(
            cost_tracker=self._cost_tracker,
            agent_id=NotBlankStr(str(owner.id)),
            task_id=str(task.id),
            # Owner-run planning session, not a registered system prompt class.
            purpose=None,
            call_category=LLMCallCategory.SYSTEM,
        ):
            return await loop.execute(
                context=ctx,
                provider=provider,
                tool_invoker=invoker,
                budget_checker=self._budget_checker(),
                shutdown_checker=self._shutdown_checker,
                completion_config=CompletionConfig(
                    temperature=self._config.temperature
                ),
            )

    def _build_invoker(
        self,
        task: Task,
        owner: AgentIdentity,
        capture: PlanCapture,
        context: DecompositionContext,
    ) -> tuple[ToolInvoker, tuple[str, ...]]:
        """Assemble the session's tool invoker over the submit + read tools.

        Returns:
            An ``(invoker, granted_tool_names)`` pair naming the terminal submit
            tool plus every read-only planning tool kept. The names travel back
            so the brief can list what the session actually holds instead of
            describing a toolkit it was never given.
        """
        submit_tool = SubmitDecompositionPlanTool(
            parent_task_id=NotBlankStr(str(task.id)),
            capture=capture,
            available_roles=context.available_roles,
            # The criteria this level is answerable for, so a plan advancing
            # none of them, or claiming one the objective never stated, is
            # refused where the agent can still fix it. Read off the context
            # rather than the task: below the root the two differ, and the
            # task's own are the prose the level above wrote about this unit
            # rather than the objective the tree is being built for.
            objective_criteria=context.objective_criteria,
            atomicity=context.atomicity,
        )
        planning_tools = self._planning_tools(task, owner)
        tools: list[BaseTool] = [submit_tool, *planning_tools]
        registry = ToolRegistry(tools)
        invoker = ToolInvoker(
            registry,
            # No permission checker and no security interceptor, because the
            # registry above is CLOSED: one terminal submit tool plus the
            # owner's read-only planning tools, nothing that writes, spends or
            # reaches outside the process. Both would be inert, and so would
            # the agent's binding, whose only consumer is the interceptor's
            # judge-independence comparison. Granting this session a tool that
            # acts makes all three live decisions again.
            permission_checker=None,
            agent_id=str(owner.id),
            # Tag tool-execution cost with the objective task so planning spend
            # attributes under the owner + task (matches cost_recording_scope);
            # this is an attribution label, not a task-lifecycle binding.
            task_id=str(task.id),
            cost_tracker=self._cost_tracker,
        )
        return invoker, tuple(tool.name for tool in tools)

    def _planning_tools(
        self,
        task: Task,
        owner: AgentIdentity,
    ) -> tuple[BaseTool, ...]:
        """Resolve the owner's read-only planning tools from the provider.

        Returns:
            The provider's tools filtered to the read-only allowlist (an
            empty tuple when no provider is wired); any write-capable tool is
            logged and dropped so the session cannot mutate state.
        """
        if self._tool_provider is None:
            return ()
        built = self._tool_provider.build_tools(
            owner_id=str(owner.id),
            project_id=task.project,
        )
        kept: list[BaseTool] = []
        for tool in built:
            if tool.action_type in _READ_ONLY_ACTION_TYPES:
                kept.append(tool)
                continue
            logger.warning(
                DECOMPOSITION_SESSION_TOOL_DROPPED,
                task_id=str(task.id),
                owner_id=str(owner.id),
                tool_name=tool.name,
                action_type=tool.action_type,
            )
        return tuple(kept)

    async def _build_context(
        self,
        task: Task,
        context: DecompositionContext,
        owner: AgentIdentity,
        granted_tools: tuple[str, ...],
    ) -> AgentContext:
        """Build the owner-persona planning context for the session.

        Between the persona system prompt and the planning brief, a compact
        org-playbook / past-retro digest is spliced in when a planning-memory
        strategy is wired, so the plan carries prior learnings even if the
        owner never calls the recall tool.

        Returns:
            An :class:`AgentContext` carrying the owner persona, an optional
            memory digest, and the fenced planning brief.
        """
        ctx = AgentContext.from_identity(owner, max_turns=self._config.max_turns)
        # Every granted tool starts LOADED. The loop offers the provider only
        # the loaded tools plus the progressive-disclosure trio, and this
        # session is deliberately not granted that trio: its whole toolkit is
        # one terminal submit tool plus a few read tools. Left unloaded, the
        # session was offered nothing at all and could never call the tool it
        # exists to call, so it ran a turn, said something, and terminated
        # 'completed' having submitted no plan, every single time.
        ctx = ctx.model_copy(
            update={
                "loaded_tools": frozenset(granted_tools),
                "tool_load_order": granted_tools,
            }
        )
        ctx = ctx.with_message(
            ChatMessage(
                role=MessageRole.SYSTEM,
                # The brief fences the workspace listing as well as the
                # objective, so the directive has to name both tags or the
                # model is never told to distrust the one an agent authored.
                content=render_agent_system_prompt(
                    owner, fences=PLANNING_SESSION_FENCES
                ),
            ),
        )
        for message in await self._recall_digest(task, owner):
            ctx = ctx.with_message(message)
        return ctx.with_message(
            ChatMessage(
                role=MessageRole.USER,
                content=planning_brief(task, context, granted_tools),
            ),
        )

    async def _recall_digest(
        self,
        task: Task,
        owner: AgentIdentity,
    ) -> tuple[ChatMessage, ...]:
        """Pre-retrieve the org/retro memory digest for the planning brief.

        Best-effort: the injection strategy degrades gracefully on retrieval
        failure, and a zero budget or an unwired strategy yields no messages.

        Returns:
            The digest messages to splice in, possibly empty.
        """
        if self._planning_memory is None or self._config.memory_digest_budget <= 0:
            return ()
        request = MemoryRecallRequest(
            agent_id=NotBlankStr(str(owner.id)),
            task_title=NotBlankStr(task.title),
            objective=task.description,
            role=owner.role,
            department=owner.department,
            project_id=task.project,
            token_budget=self._config.memory_digest_budget,
        )
        return await self._planning_memory.prepare_messages(request)

    def _budget_checker(self) -> BudgetChecker | None:
        """Build the per-session spend-ceiling checker.

        Returns:
            A checker that halts the loop once either configured bound is
            reached, or ``None`` when neither is set.
        """
        return build_session_budget_checker(self._config.ceilings)

    @staticmethod
    def _check_depth(context: DecompositionContext) -> None:
        """Raise if the recursion depth limit is reached.

        Raises:
            DecompositionDepthError: If current depth meets or exceeds max
                depth.
        """
        if context.current_depth >= depth_budget(context):
            msg = (
                f"Decomposition depth {context.current_depth} "
                f"meets or exceeds max depth {depth_budget(context)}"
            )
            logger.warning(DECOMPOSITION_VALIDATION_ERROR, error=msg)
            raise DecompositionDepthError(msg)
