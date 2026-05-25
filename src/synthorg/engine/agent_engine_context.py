"""Context preparation mixin for :class:`AgentEngine`."""

import asyncio
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import (
    ProjectAgentNotMemberError,
    ProjectNotFoundError,
)
from synthorg.engine.prompt import build_system_prompt
from synthorg.engine.prompt_validation import format_task_instruction
from synthorg.engine.task_sync import transition_task_if_needed
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ERROR,
    EXECUTION_PROJECT_VALIDATION_FAILED,
)
from synthorg.observability.events.prompt import (
    PROMPT_PERSONALITY_NOTIFY_FAILED,
    PROMPT_PERSONALITY_TRIMMED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.task import Task
    from synthorg.engine.prompt import SystemPrompt
    from synthorg.security.autonomy.models import EffectiveAutonomy
    from synthorg.tools.protocol import ToolInvokerProtocol

logger = get_logger(__name__)


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

    # Slot attrs populated on the concrete ``AgentEngine``; typed as
    # ``Any`` here because the mixin only reads them. The concrete
    # class carries the authoritative type.
    _budget_enforcer: Any
    _config_resolver: Any
    _task_engine: Any
    _personality_trim_notifier: Any
    _project_repo: Any

    async def _prepare_context(  # noqa: PLR0913
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
            except Exception as exc:
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
        system_prompt = build_system_prompt(
            agent=identity,
            task=task,
            l1_summaries=l1_summaries,
            effective_autonomy=effective_autonomy,
            currency=cur_code,
            model_tier=identity.model.model_tier,
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
        )
        ctx = ctx.with_message(
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt.content),
        )
        for msg in memory_messages:
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
            async with asyncio.timeout(2.0):
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
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PROMPT_PERSONALITY_NOTIFY_FAILED,
                agent_id=agent_id,
                agent_name=agent_name,
                task_id=task_id,
                trim_tier=trim_tier,
                reason="notifier callback raised",
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
        except Exception as exc:
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
    ) -> float:
        """Validate project existence and agent membership.

        Returns:
            The project's budget cap (``0.0`` when the task has no
            project context, otherwise ``float(project.budget)``).

        Raises:
            ProjectNotFoundError: If the project referenced by
                ``task.project`` is not in the project repository.
            ProjectAgentNotMemberError: If the project has a non-empty
                team that does not include ``agent_id``.
        """
        if not task.project:
            return 0.0
        project = await self._project_repo.get(task.project)
        if project is None:
            logger.warning(
                EXECUTION_PROJECT_VALIDATION_FAILED,
                agent_id=agent_id,
                task_id=task_id,
                project_id=task.project,
                reason="project_not_found",
            )
            raise ProjectNotFoundError(project_id=task.project)
        if project.team and agent_id not in project.team:
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
                project_id=project.id,
                project_budget=project.budget,
            )
        return float(project.budget)
