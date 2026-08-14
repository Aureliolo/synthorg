"""Context preparation mixin for :class:`AgentEngine`."""

import asyncio
from typing import TYPE_CHECKING, Final, Literal, TypedDict, cast

from pydantic import TypeAdapter

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import (
    ProjectAgentNotMemberError,
    ProjectNotFoundError,
    ProjectRepositoryNotConfiguredError,
)
from synthorg.engine.loop_turn_budget import resolve_turn_extensions
from synthorg.engine.loop_unresolved_tools import resolve_max_unresolved_tool_turns
from synthorg.engine.prompt import SystemPrompt, build_system_prompt
from synthorg.engine.prompt_validation import format_task_instruction
from synthorg.engine.task_sync import transition_task_if_needed
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
from synthorg.observability.events.prompt import (
    PROMPT_PERSONALITY_NOTIFY_FAILED,
    PROMPT_PERSONALITY_TRIMMED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage
from synthorg.tools.protocol import ToolInvokerProtocol

if TYPE_CHECKING:
    from synthorg.budget.enforcer import BudgetEnforcer
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine.agent_engine import PersonalityTrimNotifier
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.memory.injection import MemoryInjectionStrategyProvider
    from synthorg.persistence.project_protocol import ProjectRepository
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

# Token cap for memories surfaced into an agent's pre-execution context by a
# wired memory injection strategy.  Caps the injected-memory section so it
# cannot crowd out the system prompt and task instruction.
_DEFAULT_MEMORY_TOKEN_BUDGET: Final[int] = 2000
# Best-effort budget for the personality-trim WebSocket notifier callback; a
# slow dashboard sink must not stall the engine's trim path.
_PERSONALITY_TRIM_NOTIFY_TIMEOUT_S: Final[float] = 2.0
# ``NotBlankStr(x)`` is a bare ``str(x)`` cast at runtime and performs no
# validation, so a module-level adapter enforces the not-blank contract on the
# identifiers before they cross the memory-injection strategy boundary (the
# established pattern in ``post_execution/memory_hooks.py``).
_NB_ADAPTER: Final = TypeAdapter(NotBlankStr)


class PersonalityTrimPayload(TypedDict):
    """Typed payload emitted for personality-trim notifications."""

    agent_id: str
    agent_name: str
    task_id: str
    before_tokens: int
    after_tokens: int
    max_tokens: int
    trim_tier: Literal[1, 2, 3]
    budget_met: bool


class AgentEngineContextMixin:
    """Mixin providing context preparation and project validation."""

    # Slot attrs populated on the concrete ``AgentEngine``; declared here
    # so the type checker sees them when this mixin reads them. The
    # concrete class owns the assignment.
    _budget_enforcer: BudgetEnforcer | None
    _config_resolver: ConfigResolver | None
    _task_engine: TaskEngine | None
    _personality_trim_notifier: PersonalityTrimNotifier | None
    _project_repo: ProjectRepository | None
    _memory_injection_strategy_provider: MemoryInjectionStrategyProvider | None

    async def _prepare_context(
        self,
        *,
        identity: AgentIdentity,
        task: Task,
        agent_id: str,
        task_id: str,
        max_turns: int,
        memory_messages: tuple[ChatMessage, ...],
        tool_invoker: ToolInvokerProtocol | None = None,
        effective_autonomy: EffectiveAutonomy | None = None,
    ) -> tuple[AgentContext, SystemPrompt]:
        """Build system prompt and prepare execution context.

        Returns:
            ``(ctx, system_prompt)``: the prepared :class:`AgentContext`
            with memory and instruction messages threaded in and the
            corresponding :class:`SystemPrompt` (post-personality-trim
            where applicable).
        """
        l1_summaries = tool_invoker.get_l1_summaries() if tool_invoker else ()
        cur_code = (
            self._budget_enforcer.currency
            if self._budget_enforcer is not None
            else DEFAULT_CURRENCY
        )
        trimming_enabled = True
        tokens_override: int | None = None
        if self._config_resolver is not None:
            try:
                resolved_enabled = await self._config_resolver.get_bool(
                    "engine",
                    "personality_trimming_enabled",
                )
                resolved_override = await self._config_resolver.get_int(
                    "engine",
                    "personality_max_tokens_override",
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # lint-allow: swallow-ok -- best-effort side channel
                reraise_critical(exc)
                logger.warning(
                    EXECUTION_ENGINE_ERROR,
                    agent_id=agent_id,
                    task_id=task_id,
                    note="failed to read ENGINE settings, using defaults",
                    failed_keys=(
                        "personality_trimming_enabled",
                        "personality_max_tokens_override",
                    ),
                    fallback_trimming_enabled=True,
                    fallback_tokens_override=None,
                )
            else:
                trimming_enabled = resolved_enabled
                if resolved_override > 0:
                    tokens_override = resolved_override
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
            capability=identity.model.capability,
            personality_trimming_enabled=trimming_enabled,
            max_personality_tokens_override=tokens_override,
        )

        if system_prompt.personality_trim_info is not None:
            ti = system_prompt.personality_trim_info
            trim_payload: PersonalityTrimPayload = {
                "agent_id": agent_id,
                "agent_name": identity.name,
                "task_id": task_id,
                "before_tokens": ti.before_tokens,
                "after_tokens": ti.after_tokens,
                "max_tokens": ti.max_tokens,
                "trim_tier": ti.trim_tier,  # type: ignore[typeddict-item]
                "budget_met": ti.budget_met,
            }
            logger.info(PROMPT_PERSONALITY_TRIMMED, **trim_payload)
            await self._maybe_notify_personality_trim(trim_payload)

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
        )
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt.content),
        )
        injected = await self._retrieve_injected_memory_messages(
            agent_id=agent_id,
            task=task,
            identity=identity,
        )
        for msg in (*injected, *memory_messages):
            ctx = ctx.with_message(msg)
        ctx = ctx.with_message(
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

    async def _retrieve_injected_memory_messages(
        self,
        *,
        agent_id: str,
        task: Task,
        identity: AgentIdentity,
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
        strategy = (
            None
            if self._memory_injection_strategy_provider is None
            else self._memory_injection_strategy_provider()
        )
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

    async def _maybe_notify_personality_trim(
        self,
        payload: PersonalityTrimPayload,
    ) -> None:
        """Publish a personality-trim WebSocket notification, best-effort."""
        if self._personality_trim_notifier is None:
            return
        notify_enabled = await self._read_notify_enabled(payload)
        if not notify_enabled:
            return
        agent_id = payload["agent_id"]
        agent_name = payload["agent_name"]
        task_id = payload["task_id"]
        trim_tier = payload["trim_tier"]
        try:
            async with asyncio.timeout(_PERSONALITY_TRIM_NOTIFY_TIMEOUT_S):
                await self._personality_trim_notifier(payload)
        except TimeoutError:
            logger.warning(
                PROMPT_PERSONALITY_NOTIFY_FAILED,
                agent_id=agent_id,
                agent_name=agent_name,
                task_id=task_id,
                trim_tier=trim_tier,
                reason="notifier callback timed out (>2s)",
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort notification
            reraise_critical(exc)
            logger.warning(
                PROMPT_PERSONALITY_NOTIFY_FAILED,
                agent_id=agent_id,
                agent_name=agent_name,
                task_id=task_id,
                trim_tier=trim_tier,
                reason="notifier callback raised",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _read_notify_enabled(
        self,
        payload: PersonalityTrimPayload,
    ) -> bool:
        """Read the ``personality_trimming_notify`` setting, fail-open.

        Returns:
            The resolved bool, or ``True`` (fail-open) when no resolver
            is wired or the resolver raises.
        """
        if self._config_resolver is None:
            return True
        try:
            result: bool = await self._config_resolver.get_bool(
                "engine",
                "personality_trimming_notify",
            )
            return result  # noqa: TRY300
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort notification
            reraise_critical(exc)
            logger.warning(
                PROMPT_PERSONALITY_NOTIFY_FAILED,
                agent_id=payload["agent_id"],
                agent_name=payload["agent_name"],
                task_id=payload["task_id"],
                trim_tier=payload["trim_tier"],
                reason=(
                    "failed to read personality_trimming_notify setting;"
                    " fail-open with default notify_enabled=True"
                ),
            )
            return True

    async def _validate_project(
        self,
        *,
        task: Task,
        agent_id: str,
        task_id: str,
        is_system: bool = False,
    ) -> float:
        """Validate project existence and agent membership.

        Args:
            task: The task about to run.
            agent_id: The agent that would run it.
            task_id: The task identifier, for the log.
            is_system: Whether the runner is a built-in gate rather than a
                member of the organisation. The membership half of this
                check confines a WORKING agent to its project; a gate judges
                across projects and must stay independent of the executor,
                so it is deliberately on no team and is exempt. Existence is
                still checked for both, because a project that is not there
                is a broken dispatch either way.

        Returns:
            The project's budget cap (``0.0`` when the task has no
            project context, otherwise ``float(project.budget)``).

        Raises:
            ProjectNotFoundError: If the project referenced by
                ``task.project`` is not in the project repository.
            ProjectAgentNotMemberError: If the project has a non-empty
                team that does not include ``agent_id``, and the runner is
                not a system gate.
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
        if project.team and agent_id not in project.team and not is_system:
            logger.warning(
                EXECUTION_PROJECT_VALIDATION_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                project_id=task.project,
                reason="agent_not_in_team",
            )
            raise ProjectAgentNotMemberError(
                project_id=task.project,
                agent_id=agent_id,
            )
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
