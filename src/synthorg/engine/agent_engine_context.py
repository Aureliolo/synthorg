"""Context preparation mixin for :class:`AgentEngine`."""

import asyncio
from typing import TYPE_CHECKING, Final, NamedTuple, cast

from pydantic import TypeAdapter

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.session_budget import SessionBudgetChecker
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine._ceiling_publish import ctx_ceiling_values
from synthorg.engine._ceiling_sync import ceiling_synced_task
from synthorg.engine.context import AgentContext
from synthorg.engine.context_budget import make_context_indicator
from synthorg.engine.errors import (
    ProjectNotFoundError,
    ProjectRepositoryNotConfiguredError,
)
from synthorg.engine.loop_protocol import make_budget_checker
from synthorg.engine.loop_turn_budget import resolve_turn_extensions
from synthorg.engine.loop_unresolved_tools import resolve_max_unresolved_tool_turns
from synthorg.engine.prompt import SystemPrompt, build_system_prompt
from synthorg.engine.prompt_validation import format_task_instruction
from synthorg.engine.routing_policy.capability_policy import (
    CapabilityPolicy,
    described_capability,
)
from synthorg.engine.task_sync import transition_task_if_needed
from synthorg.memory.injection import MemoryInjectionStrategy
from synthorg.memory.recall_request import MemoryRecallRequest
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ERROR,
    EXECUTION_PROJECT_VALIDATION_FAILED,
)
from synthorg.observability.events.memory import (
    MEMORY_CONTEXT_INJECTED,
    MEMORY_CONTEXT_INJECTION_FAILED,
)
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.protocol import ToolInvokerProtocol

if TYPE_CHECKING:
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.memory.injection import MemoryInjectionStrategyProvider
    from synthorg.persistence.cost_forecast_protocol import CostForecastRepository
    from synthorg.persistence.project_protocol import ProjectRepository
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

# Token cap for memories surfaced into an agent's pre-execution context by a
# wired memory injection strategy.  Caps the injected-memory section so it
# cannot crowd out the system prompt and task instruction.
_DEFAULT_MEMORY_TOKEN_BUDGET: Final[int] = 2000
# ``NotBlankStr(x)`` is a bare ``str(x)`` cast at runtime and performs no
# validation, so a module-level adapter enforces the not-blank contract on the
# identifiers before they cross the memory-injection strategy boundary (the
# established pattern in ``post_execution/memory_hooks.py``).
_NB_ADAPTER: Final = TypeAdapter(NotBlankStr)


class MemoryContextInputs(NamedTuple):
    """What memory contributes to one unit of work's context.

    The two travel together because they are resolved together, once per
    unit of work: the strategy that retrieves more, and whatever the caller
    already holds. Passing the strategy rather than re-reading it is what
    keeps a task's memory tools and its injected context on one backend.

    Attributes:
        messages: Memory messages the caller already has.
        strategy: The strategy wired when this unit of work started, or
            ``None`` when memory is unwired.
    """

    messages: tuple[ChatMessage, ...]
    strategy: MemoryInjectionStrategy | None


class RunExecutionDeps(NamedTuple):
    """What a run's loop execution needs, resolved once by the caller.

    Grouped for the same reason as :class:`MemoryContextInputs`: the three
    travel together from one construction site (``AgentEngine.execute``'s
    fresh-run and resume paths) through ``_prepare_context`` and on into the
    loop, so a single parameter here is one fewer thing for that path to
    keep in sync.

    Attributes:
        provider: The bound completion provider for this run.
        budget_checker: The checker this run's turns are measured against,
            or ``None`` when every bound is disabled.
        tool_invoker: The tool invoker for this run, or ``None`` when the
            identity carries no tools.
    """

    provider: CompletionProvider
    budget_checker: SessionBudgetChecker | None
    tool_invoker: ToolInvokerProtocol | None


class AgentEngineContextMixin:
    """Mixin providing context preparation and project validation."""

    # Slot attrs populated on the concrete ``AgentEngine``; declared here
    # so the type checker sees them when this mixin reads them. The
    # concrete class owns the assignment.
    _budget_enforcer: BudgetEnforcer | None
    _capability: CapabilityPolicy | None
    _config_resolver: ConfigResolver | None
    _cost_forecast_repo: CostForecastRepository | None
    _task_engine: TaskEngine | None
    _project_repo: ProjectRepository | None
    _memory_injection_strategy_provider: MemoryInjectionStrategyProvider | None

    async def _build_budget_checker(
        self,
        task: Task,
        agent_id: str,
        *,
        project_id: str | None,
        project_budget: float = 0.0,
    ) -> SessionBudgetChecker | None:
        """Build the checker this run's turns are measured against.

        The single owner of that construction: a caller reads
        :attr:`SessionBudgetChecker.ceilings` off exactly the object it is
        about to pass to the loop, rather than resolving the same ceiling a
        second time to render it. The fresh-run path (before the prompt is
        built, so the declaration can go in it), the approval-resume path
        (before ``AgentExecuteRequest`` is built) and the checkpoint-resume
        path (before the reconstituted context re-enters the loop) all call
        here.

        Args:
            task: The task the checker enforces against.
            agent_id: Agent identifier for logging.
            project_id: The project the checker is scoped to. Required
                rather than defaulted, so each caller states its own answer
                instead of inheriting one that happens to suit a different
                caller: the checkpoint-resume path threads a project id that
                can diverge from ``task.project`` before a project repo is
                wired, so ``task.project`` is not a safe default here.
            project_budget: Total project budget (0 = disabled).

        Returns:
            The checker, or ``None`` when every bound is disabled.
        """
        if self._budget_enforcer is not None:
            return await self._budget_enforcer.make_budget_checker(
                await ceiling_synced_task(task, self._cost_forecast_repo),
                agent_id,
                project_id=project_id,
                project_budget=project_budget,
            )
        return make_budget_checker(task)

    async def _resolve_context_capacity_tokens(
        self,
        provider: CompletionProvider,
        identity: AgentIdentity,
        *,
        agent_id: str,
        task_id: str,
    ) -> int | None:
        """Resolve the bound model's context window, for the budget gauge.

        A lookup failure degrades to ``None``, which is the value every task
        run already carried before this seam existed, so the failure mode
        cannot get worse than the status quo it replaces.

        Returns:
            The model's ``max_context_tokens``, or ``None`` on any failure.
        """
        try:
            capabilities: ModelCapabilities = await provider.get_model_capabilities(
                identity.model.model_id
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- degrade-to-unknown-capacity wiring
            reraise_critical(exc)
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                note="context-capacity lookup failed; budget gauge fill unknown",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        return capabilities.max_context_tokens

    async def _resolve_ceilings(
        self,
        *,
        provider: CompletionProvider,
        identity: AgentIdentity,
        budget_checker: SessionBudgetChecker | None,
        agent_id: str,
        task_id: str,
    ) -> tuple[float | None, int | None, int | None]:
        """Resolve what this run's context should be constructed with.

        ``budget_checker.ceilings.as_optionals()`` is the translation between
        ``SessionCeilings``'s "disabled" (0) and ``AgentContext``'s (``None``,
        a ``gt=0`` field): a verbatim stamp of a genuinely-zero money bound (a
        flat-rate connection) would fail the context's own validation.

        Returned rather than stamped via ``model_copy``: ``AgentContext``'s
        ``gt=0`` / no-NaN constraints on these three fields only validate
        through the constructor, per ``from_identity``'s own docstring, so
        the caller must pass them into ``AgentContext.from_identity``
        itself.

        Returns:
            ``(cost_ceiling, token_ceiling, context_capacity_tokens)``.
        """
        cost_ceiling, token_ceiling = ctx_ceiling_values(budget_checker)
        context_capacity_tokens = await self._resolve_context_capacity_tokens(
            provider, identity, agent_id=agent_id, task_id=task_id
        )
        return cost_ceiling, token_ceiling, context_capacity_tokens

    async def _prepare_context(
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        max_turns: int,
        memory: MemoryContextInputs,
        execution: RunExecutionDeps,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> tuple[AgentContext, SystemPrompt]:
        """Build system prompt and prepare execution context.

        Returns:
            ``(ctx, system_prompt)``: the prepared :class:`AgentContext`
            with memory and instruction messages threaded in and the
            corresponding :class:`SystemPrompt`.
        """
        l1_summaries = (
            execution.tool_invoker.get_l1_summaries() if execution.tool_invoker else ()
        )
        cur_code = (
            self._budget_enforcer.currency
            if self._budget_enforcer is not None
            else DEFAULT_CURRENCY
        )
        (
            cost_ceiling,
            token_ceiling,
            context_capacity_tokens,
        ) = await self._resolve_ceilings(
            provider=execution.provider,
            identity=identity,
            budget_checker=execution.budget_checker,
            agent_id=agent_id,
            task_id=task_id,
        )

        # Built before the prompt so the declaration below reads the exact
        # ceilings the run will be measured against, rather than resolving
        # them a second time to render them.
        ctx = AgentContext.from_identity(
            identity,
            task=task,
            max_turns=max_turns,
            turn_extensions=await resolve_turn_extensions(
                self._config_resolver, agent_id=agent_id, task_id=task_id
            ),
            max_unresolved_tool_turns=await resolve_max_unresolved_tool_turns(
                self._config_resolver, agent_id=agent_id, task_id=task_id
            ),
            context_capacity_tokens=context_capacity_tokens,
            cost_ceiling=cost_ceiling,
            token_ceiling=token_ceiling,
        )
        # The declaration renders once, at zero spend: an honest reading of
        # the ceiling's magnitude, not a live percentage that would go stale
        # for the rest of the session. The turn boundary (loop_budget_signal)
        # is what reports the live remainder as the run proceeds.
        indicator = (
            make_context_indicator(ctx)
            if token_ceiling is not None or context_capacity_tokens is not None
            else None
        )
        # build_system_prompt is pure CPU + blocking strategy-pack file reads
        # (principles.py) and runs on every agent turn; offload it so the
        # per-turn prompt build never stalls the event loop. It is sync-only
        # and touches no shared mutable state (its inputs are frozen models /
        # value types), so running it on a worker thread is safe.
        system_prompt = await asyncio.to_thread(
            build_system_prompt,
            agent=identity,
            task=task,
            l1_summaries=l1_summaries,
            effective_autonomy=effective_autonomy,
            currency=cur_code,
            capability=described_capability(self._capability, identity.model),
            context_budget_indicator=indicator.format() if indicator else None,
        )
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt.content),
        )
        injected = await self._retrieve_injected_memory_messages(
            agent_id=agent_id,
            task=task,
            identity=identity,
            memory_strategy=memory.strategy,
        )
        for msg in (*injected, *memory.messages):
            ctx = ctx.with_message(msg)
        # PINNED, and it is the only message the loop pins. This is the
        # single statement of what the agent was asked to do, and it is a
        # USER message: compaction keeps leading SYSTEM messages verbatim
        # and snippets ASSISTANT ones, so an unpinned brief ages out with
        # nothing left of it, and a resumed session then works from a
        # summary of its own replies.
        ctx = ctx.with_pinned_message(
            ChatMessage(
                role=MessageRole.USER,
                content=format_task_instruction(task, currency=cur_code),
            ),
        )

        ctx = await transition_task_if_needed(
            ctx,
            agent_id,
            task_id,
            self._task_engine,
        )
        return ctx, system_prompt

    async def _resolve_memory_token_budget(self, *, agent_id: str, task_id: str) -> int:
        """Resolve the injected-memory token cap, failing safe to the default.

        Returns:
            The operator-configured ``engine.memory_context_token_budget``
            when a resolver is wired and the value is positive, else
            :data:`_DEFAULT_MEMORY_TOKEN_BUDGET`.
        """
        if self._config_resolver is None:
            return _DEFAULT_MEMORY_TOKEN_BUDGET
        try:
            resolved = await self._config_resolver.get_int(
                "engine", "memory_context_token_budget"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- degrade-to-None wiring
            reraise_critical(exc)
            logger.warning(
                EXECUTION_ENGINE_ERROR,
                agent_id=agent_id,
                task_id=task_id,
                note="failed to read memory_context_token_budget, using default",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return _DEFAULT_MEMORY_TOKEN_BUDGET
        return resolved if resolved > 0 else _DEFAULT_MEMORY_TOKEN_BUDGET

    def _resolve_memory_strategy(self) -> MemoryInjectionStrategy | None:
        """Read the strategy that is wired right now.

        Called once per unit of work, never per collaborator that needs it.
        The reconciler can replace a backend at any moment, so two reads
        separated by an ``await`` can return two different strategies, and a
        task that registered its memory tools from one while injecting its
        context from the other would recall against a backend its tools do
        not write to.

        Returns:
            The current strategy, or ``None`` when memory is unwired.
        """
        if self._memory_injection_strategy_provider is None:
            return None
        return self._memory_injection_strategy_provider()

    async def _retrieve_injected_memory_messages(
        self,
        *,
        agent_id: str,
        task: Task,
        identity: AgentIdentity,
        memory_strategy: MemoryInjectionStrategy | None,
    ) -> tuple[ChatMessage, ...]:
        """Retrieve memories to inject into context via the wired strategy.

        Resolved per task rather than captured once, so an engine built while
        the memory backend was still unwired starts recalling as soon as it
        comes up. A captured value would give that engine no way to reach a
        backend wired after it: every task would run with no recall, silently,
        for the rest of the process's life.

        Presence-gated: returns ``()`` when no provider is wired, or when the
        provider reports no strategy, so construction sites that do not opt in
        are unaffected.  A CONTEXT strategy returns formatted, marker-wrapped
        memories; a TOOL_BASED strategy returns ``()`` (it surfaces memories
        via agent tools, not pre-execution context).  ``prepare_messages``
        degrades gracefully on retrieval failure; system errors propagate, and
        any other unexpected error is swallowed so memory enrichment never
        fails the run.

        The recall query carries the surrounding work context, not the task
        title alone: the memory that helps is often phrased in the vocabulary
        of the description, the role or the project rather than of the title.

        Returns:
            The memory messages to thread into the agent's context (possibly
            empty).
        """
        strategy = memory_strategy
        if strategy is None:
            return ()
        token_budget = await self._resolve_memory_token_budget(
            agent_id=agent_id, task_id=str(task.id)
        )
        request = MemoryRecallRequest(
            agent_id=_NB_ADAPTER.validate_python(agent_id),
            task_title=_NB_ADAPTER.validate_python(task.title),
            objective=task.description,
            role=identity.role,
            department=identity.department,
            project_id=task.project or None,
            token_budget=token_budget,
        )
        try:
            messages: tuple[ChatMessage, ...] = await strategy.prepare_messages(request)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort memory hook
            reraise_critical(exc)
            logger.warning(
                MEMORY_CONTEXT_INJECTION_FAILED,
                agent_id=agent_id,
                task_id=task.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ()
        if messages:
            logger.info(
                MEMORY_CONTEXT_INJECTED,
                agent_id=agent_id,
                task_id=task.id,
                message_count=len(messages),
            )
        return messages

    async def _validate_project(
        self,
        *,
        task: Task,
        agent_id: str,
        task_id: str,
    ) -> float:
        """Validate project existence and enforce its budget.

        A project admits the whole roster, so there is no membership to check
        here. What confines an agent to one initiative is structural rather
        than a stored list, and it is keyed on the TASK's project rather than
        on the agent, so it holds however the agent got here: the workspace
        root is ``<repo_root>/projects/<id>`` with path escape refused, and
        the sandbox container reuse key carries the project, forcing a
        teardown when it changes. The governed connection surfaces (forge
        repo scope, the MCP tool surfaces, the SecOps action-type gate) bound
        the same agent everywhere rather than per project, so they are a
        separate boundary and not a replacement for this one.

        Args:
            task: The task about to run.
            agent_id: The agent that would run it, for the log.
            task_id: The task identifier, for the log.

        Returns:
            The project's budget cap (``0.0`` when the task has no
            project context, otherwise ``float(project.budget)``).

        Raises:
            ProjectNotFoundError: If the project referenced by
                ``task.project`` is not in the project repository.
        """
        if not task.project:
            return 0.0
        # Project validation is only reached on the wired-repo path; the
        # concrete engine assigns ``_project_repo`` before this runs.
        repo = cast("ProjectRepository", self._project_repo)
        project = await repo.get(task.project)
        if project is None:
            logger.warning(
                EXECUTION_PROJECT_VALIDATION_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                project_id=task.project,
                reason="project_not_found",
            )
            raise ProjectNotFoundError(project_id=task.project)
        if self._budget_enforcer is not None and project.budget > 0:
            await self._budget_enforcer.check_project_budget(
                project_id=str(project.id),
                project_budget=project.budget,
            )
        return float(project.budget)

    def _reject_unconfigured_project_repo(
        self,
        *,
        task: Task,
        agent_id: str,
        task_id: str,
    ) -> None:
        """Warn, and fail loud for a work task, when no project repo is wired.

        Reached only when no ``_project_repo`` is configured but the task
        still carries a project reference. A work task (one expecting
        artifacts) will produce output against the project, so running it
        with no repository to validate membership/budget is a correctness
        and security gap; it aborts. A non-work task carries the project
        only as a label and proceeds with the logged warning.

        Raises:
            ProjectRepositoryNotConfiguredError: When ``task`` expects
                artifacts but no project repository is configured.
        """
        logger.warning(
            EXECUTION_PROJECT_VALIDATION_FAILED,
            agent_id=agent_id,
            task_id=task_id,
            project_id=task.project,
            reason="project_repo_not_configured",
        )
        if task.artifacts_expected:
            raise ProjectRepositoryNotConfiguredError(project_id=task.project)
