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

from typing import Final, cast, override

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_persona import render_agent_system_prompt
from synthorg.engine.context import AgentContext
from synthorg.engine.decomposition.llm_parse import args_to_decomposition_plan
from synthorg.engine.decomposition.llm_prompt import (
    build_decomposition_tool,
    safe_roles,
)
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
)
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.decomposition.tool_provider import DecompositionToolProvider
from synthorg.engine.errors import (
    DecompositionDepthError,
    DecompositionError,
    DecompositionSubtaskLimitError,
)
from synthorg.engine.loop_protocol import (
    BudgetChecker,
    ExecutionResult,
    ShutdownChecker,
)
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
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
    DECOMPOSITION_SESSION_DUPLICATE_SUBMIT,
    DECOMPOSITION_SESSION_FALLBACK,
    DECOMPOSITION_SESSION_NO_PLAN,
    DECOMPOSITION_SESSION_STARTED,
    DECOMPOSITION_SESSION_TOOL_DROPPED,
    DECOMPOSITION_VALIDATION_ERROR,
)
from synthorg.providers.cost_recording import cost_recording_scope
from synthorg.providers.enums import MessageRole
from synthorg.providers.errors import DriverNotRegisteredError
from synthorg.providers.models import ChatMessage, CompletionConfig
from synthorg.providers.protocol import CompletionProvider, ProviderSelector
from synthorg.security.autonomy.enums import ActionType, ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.invoker import ToolInvoker
from synthorg.tools.registry import ToolRegistry

logger = get_logger(__name__)

_STRATEGY_NAME = "agent-session"

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


class AgentSessionDecompositionConfig(BaseModel):
    """Configuration for the agent-session decomposition strategy.

    Attributes:
        max_turns: Hard turn cap for the planning session.
        temperature: Sampling temperature for the planning turns.
        cost_ceiling: Per-session spend ceiling (base currency); the session
            halts once accumulated cost reaches it.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_turns: int = Field(default=12, ge=1, le=50, description="Planning turn cap")
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    cost_ceiling: float = Field(
        default=2.0,
        gt=0.0,
        description="Per-session spend ceiling in the base currency",
    )
    memory_digest_budget: int = Field(
        default=1000,
        ge=0,
        description="Token cap for the org/retro memory digest injected into "
        "the planning brief; 0 injects nothing (the tool grant still applies)",
    )


def _toolkit_lines(granted_tools: tuple[str, ...]) -> tuple[str, ...]:
    """Render the brief's account of what this session can actually call.

    Derived from the built registry rather than written out, so the brief
    cannot advertise a toolkit the session does not hold. Told to guess, the
    planner reached for a progressive-disclosure trio (``list_tools``,
    ``load_tool``, ``load_tool_resource``) it was never granted and burned two
    rounds on tool-not-found before producing nothing.

    Args:
        granted_tools: Names of every tool in the session's registry.

    Returns:
        The brief lines naming the toolkit.
    """
    if not granted_tools:
        return ("You have no tools: plan from the objective alone.",)
    return (
        "You can call exactly these tools, directly, with no discovery step:",
        f"  {', '.join(granted_tools)}.",
        "There is no tool catalogue to list or load from; anything not named",
        "above does not exist in this session. Research with what you have,",
        "and where the plan turns on an external fact you cannot check, record",
        "it as an assumption rather than guessing silently.",
    )


def _roster_lines(available_roles: tuple[NotBlankStr, ...]) -> tuple[str, ...]:
    """Render the roster constraint for the planning brief.

    Stated in the brief as well as in the submit tool's schema, because the
    schema ``enum`` only reaches a provider that enforces schemas, and left to
    guess the planner produces plausible near-misses (an "Engineer" title for
    an org staffing a "Developer" one) that nothing can be dispatched to.

    Args:
        available_roles: The roles the org staffs.

    Returns:
        The brief lines, or empty when no roster is known.
    """
    if not available_roles:
        return ()
    return (
        "  This organisation staffs exactly these roles:",
        f"  {', '.join(safe_roles(available_roles))}.",
        "  Every owner must be one of them, spelled the same way. Do not",
        "  invent a role or substitute a similar-sounding title; an owner",
        "  outside this list is rejected.",
    )


class _PlanCapture:
    """Mutable holder for the plan a session submits via the terminal tool."""

    __slots__ = ("plan",)

    def __init__(self) -> None:
        self.plan: DecompositionPlan | None = None


class SubmitDecompositionPlanTool(BaseTool):
    """Terminal planning tool: the session submits its final plan through it.

    The schema mirrors the single-shot decomposition tool (so each subtask
    carries ``expected_artifacts`` + ``acceptance_criteria``); the parsed,
    id-remapped plan is captured for the strategy to return. A malformed
    submission surfaces as a tool error so the agent can correct and resubmit
    within the same session.
    """

    def __init__(
        self,
        *,
        parent_task_id: NotBlankStr,
        capture: _PlanCapture,
        available_roles: tuple[NotBlankStr, ...] = (),
    ) -> None:
        super().__init__(
            name="submit_decomposition_plan",
            description=(
                "Submit the final plan. Provide every item with its "
                "dependencies (only genuine ones, so independent work runs in "
                "parallel), an accountable owning role, calibrated stakes, "
                "expected_artifacts, and acceptance_criteria. Call this exactly "
                "once, last, after you have researched and self-reviewed."
            ),
            parameters_schema=build_decomposition_tool(
                available_roles
            ).parameters_schema,
            category=ToolCategory.OTHER,
        )
        self._parent_task_id = parent_task_id
        self._capture = capture
        self._available_roles = available_roles

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Parse + capture the submitted plan, or report a correctable error.

        Returns:
            A success result naming the accepted subtask count, or an error
            result describing why the plan was rejected so the agent retries.
        """
        try:
            plan = args_to_decomposition_plan(
                cast("dict[str, JsonValue]", arguments),
                self._parent_task_id,
                self._available_roles,
            )
        except DecompositionError as exc:
            return ToolExecutionResult(
                content=(
                    f"Plan rejected: {safe_error_description(exc)}. "
                    "Fix the issue and call submit_decomposition_plan again."
                ),
                is_error=True,
            )
        if self._capture.plan is not None:
            logger.warning(
                DECOMPOSITION_SESSION_DUPLICATE_SUBMIT,
                parent_task_id=self._parent_task_id,
                previous_subtask_count=len(self._capture.plan.subtasks),
                new_subtask_count=len(plan.subtasks),
            )
        self._capture.plan = plan
        return ToolExecutionResult(
            content=(
                f"Plan accepted with {len(plan.subtasks)} subtasks. You may stop now."
            ),
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
    async def decompose(
        self,
        task: Task,
        context: DecompositionContext,
    ) -> DecompositionPlan:
        """Plan the task via an owner-run agent session, or fall back.

        The fallback covers the cases where there is no researched plan to
        lose: no owner staffed, an unresolvable provider, or a session that
        submitted nothing. A plan that came back too big is not one of them.

        Returns:
            The decomposition plan the owner submitted, or the fallback
            strategy's plan when no owner is staffed / no plan was submitted.

        Raises:
            DecompositionDepthError: If the current depth meets or exceeds the
                configured max depth.
            DecompositionSubtaskLimitError: If the submitted plan carries more
                subtasks than the caller allowed.
            DecompositionError: If both the session and the fallback fail.
        """
        self._check_depth(context)
        owner = context.owner_identity
        if owner is None:
            logger.info(
                DECOMPOSITION_SESSION_FALLBACK,
                task_id=str(task.id),
                reason="no_owner_staffed",
            )
            return await self._fallback.decompose(task, context)

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
            return await self._fallback.decompose(task, context)

        capture = _PlanCapture()
        result = await self._run_session(task, context, owner, provider, capture)
        plan = capture.plan
        if plan is None:
            logger.warning(
                DECOMPOSITION_SESSION_NO_PLAN,
                task_id=str(task.id),
                owner_id=str(owner.id),
                termination=result.termination_reason.value,
                termination_detail=(
                    scrub_secret_tokens(result.error_message)
                    if result.error_message is not None
                    else None
                ),
            )
            return await self._fallback.decompose(task, context)

        if len(plan.subtasks) > context.max_subtasks:
            # The owner researched this plan across turns with read-only
            # tools; the single-shot fallback would produce a thinner one the
            # operator never sees. Refusing surfaces the real plan's size on
            # the durable Plan as a failure reason instead, the same as every
            # other strategy does.
            over_limit = DecompositionSubtaskLimitError(
                produced=len(plan.subtasks), limit=context.max_subtasks
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
        return plan

    async def _run_session(
        self,
        task: Task,
        context: DecompositionContext,
        owner: AgentIdentity,
        provider: CompletionProvider,
        capture: _PlanCapture,
    ) -> ExecutionResult:
        """Run the bounded planning loop as *owner*, capturing the plan.

        Args:
            task: The task being decomposed.
            context: The decomposition context (depth, owner, limits).
            owner: The staffed owner running the planning session.
            provider: The completion client for the owner's bound provider.
            capture: Sink the terminal submit tool writes the plan into.

        Returns:
            The loop's execution result (termination reason + error detail
            for observability).
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
        capture: _PlanCapture,
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
        )
        planning_tools = self._planning_tools(task, owner)
        tools: list[BaseTool] = [submit_tool, *planning_tools]
        registry = ToolRegistry(tools)
        invoker = ToolInvoker(
            registry,
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
        ctx = ctx.with_message(
            ChatMessage(
                role=MessageRole.SYSTEM,
                content=render_agent_system_prompt(owner),
            ),
        )
        for message in await self._recall_digest(task, owner):
            ctx = ctx.with_message(message)
        return ctx.with_message(
            ChatMessage(
                role=MessageRole.USER,
                content=self._planning_brief(task, context, granted_tools),
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

    def _planning_brief(
        self,
        task: Task,
        context: DecompositionContext,
        granted_tools: tuple[str, ...],
    ) -> str:
        """Compose the planning instruction with the fenced objective.

        The objective text originates from operator/charter input and is
        attacker-controllable, so it is fenced via ``wrap_untrusted``; the
        instructions and numeric constraints sit outside the fence.

        Returns:
            The user-message brief driving the planning session.
        """
        inner = [f"Title: {task.title}", f"Description: {task.description}"]
        if task.acceptance_criteria:
            inner.append("Acceptance criteria:")
            inner.extend(f"  - {c.description}" for c in task.acceptance_criteria)
        return "\n".join(
            [
                "You are the accountable owner planning this objective. Produce",
                "a plan a team would execute, not a flat checklist.",
                *_toolkit_lines(granted_tools),
                "Then build the plan:",
                "- Model real structure: add a dependency ONLY when one item",
                "  genuinely cannot start until another finishes; independent",
                "  workstreams must run in parallel (task_structure mixed or",
                "  parallel, not a single sequential chain).",
                "- Assign an accountable owning role to every item; leave none",
                "  unowned.",
                *_roster_lines(context.available_roles),
                "- Calibrate: most items are normal stakes; reserve high or",
                "  critical for irreversible or high-blast-radius work.",
                "- Give every item concrete expected_artifacts and verifiable",
                "  acceptance_criteria (never empty).",
                "- Where the plan hinges on a real choice (stack, architecture),",
                "  surface a decision item (kind 'decision') with 2-4 options and",
                "  one recommended, rather than silently deciding.",
                "Then critically self-review: is it genuinely parallel where it",
                "can be, is every item owned, are stakes calibrated (not all",
                "high), does every item define done? Finally, call",
                "submit_decomposition_plan exactly once with the complete plan.",
                "",
                wrap_untrusted(TAG_TASK_DATA, "\n".join(inner)),
                "",
                "Constraints:",
                f"  max_subtasks: {context.max_subtasks}",
            ]
        )

    def _budget_checker(self) -> BudgetChecker:
        """Build the per-session spend-ceiling checker.

        Returns:
            A checker that halts the loop once accumulated cost reaches the
            configured ceiling.
        """
        ceiling = self._config.cost_ceiling
        return lambda ctx: ctx.accumulated_cost.cost >= ceiling

    @staticmethod
    def _check_depth(context: DecompositionContext) -> None:
        """Raise if the recursion depth limit is reached.

        Raises:
            DecompositionDepthError: If current depth meets or exceeds max
                depth.
        """
        if context.current_depth >= context.max_depth:
            msg = (
                f"Decomposition depth {context.current_depth} "
                f"meets or exceeds max depth {context.max_depth}"
            )
            logger.warning(DECOMPOSITION_VALIDATION_ERROR, error=msg)
            raise DecompositionDepthError(msg)
