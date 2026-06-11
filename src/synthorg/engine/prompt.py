"""System prompt construction from agent identity and context.

Translates agent configuration (personality, skills, authority, role) into
contextually rich system prompts that shape agent behavior during LLM calls.

**Non-inferable principle:** System prompts should contain only information
that agents cannot discover by reading the codebase or environment.  Full
tool definitions are delivered via the LLM provider's API ``tools``
parameter.  However, lightweight L1 metadata (name, category, cost tier,
one-line description) IS injected into the system prompt so agents can
discover what tools exist and decide which to load via ``load_tool()``.

Example::

    from synthorg.engine.prompt import build_system_prompt

    prompt = build_system_prompt(agent=agent_identity, task=task)
    prompt.content  # rendered system prompt string
"""

from typing import TYPE_CHECKING

from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.communication.async_tasks.models import (
    AsyncTaskStateChannel,
)
from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.role import Role
from synthorg.core.task import Task
from synthorg.core.tool_disclosure import ToolL1Metadata
from synthorg.core.types import ModelTier
from synthorg.engine._prompt_helpers import build_metadata as _build_metadata
from synthorg.engine.errors import PromptBuildError
from synthorg.engine.policy_validation import validate_policy_quality
from synthorg.engine.prompt_profiles import get_prompt_profile
from synthorg.engine.prompt_render import render_with_trimming
from synthorg.engine.prompt_result import (
    SystemPrompt,
    append_async_task_section,
    log_and_return,
)
from synthorg.engine.prompt_validation import (
    resolve_template,
    validate_max_tokens,
    validate_org_policies,
)
from synthorg.engine.sanitization import sanitize_message
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.engine.token_estimation import (
    DefaultTokenEstimator,
    PromptTokenEstimator,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.prompt import (
    PROMPT_BUILD_ERROR,
    PROMPT_BUILD_START,
    PROMPT_POLICY_VALIDATION_FAILED,
    PROMPT_PROFILE_SELECTED,
)
from synthorg.observability.events.tool import TOOL_L1_INJECTED
from synthorg.providers.models import ToolDefinition

if TYPE_CHECKING:
    from synthorg.core.company import Company
    from synthorg.core.effective_autonomy import EffectiveAutonomy

__all__ = ["SystemPrompt", "build_error_prompt", "build_system_prompt"]

logger = get_logger(__name__)


def build_system_prompt(  # noqa: PLR0913
    *,
    agent: AgentIdentity,
    role: Role | None = None,
    task: Task | None = None,
    available_tools: tuple[ToolDefinition, ...] = (),
    l1_summaries: tuple[ToolL1Metadata, ...] = (),
    company: Company | None = None,
    org_policies: tuple[str, ...] = (),
    max_tokens: int | None = None,
    custom_template: str | None = None,
    token_estimator: PromptTokenEstimator | None = None,
    effective_autonomy: EffectiveAutonomy | None = None,
    context_budget_indicator: str | None = None,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    model_tier: ModelTier | None = None,
    personality_trimming_enabled: bool = True,
    max_personality_tokens_override: int | None = None,
    strategy_config: StrategyConfig | None = None,
    async_task_state: AsyncTaskStateChannel | None = None,
) -> SystemPrompt:
    """Build a system prompt from agent identity and optional context.

    When ``max_tokens`` is provided and the prompt exceeds it, optional
    sections are progressively trimmed (strategy, company, task,
    org_policies).

    Args:
        agent: Agent identity containing personality, skills, authority.
        role: Optional role with description and responsibilities.
        task: Optional task context injected into the prompt.
        available_tools: Tool definitions populated into template context
            for custom templates only; the default template omits tools
            per D22 (non-inferable principle).
        l1_summaries: L1 metadata for system prompt injection.
            Lightweight tool summaries rendered in the Available
            Tools section of the default template.
        company: Opt-in. Non-inferable principle recommends omitting
            unless agents need org-level context they cannot discover.
        org_policies: Company-wide policy texts to inject into prompt.
        max_tokens: Token budget; sections are trimmed if exceeded.
        custom_template: Optional Jinja2 template string override.
        token_estimator: Custom token estimator (defaults to char/4).
        effective_autonomy: Resolved autonomy for the current run.
        context_budget_indicator: Formatted context budget indicator
            string to inject into the prompt.
        currency: ISO 4217 currency code for budget displays.  Validated
            against the allowlist in ``synthorg.budget.currency``.
        model_tier: Model capability tier for prompt profile selection.
            ``None`` defaults to the full (large) profile.
        personality_trimming_enabled: When ``True`` (default), the
            personality section is progressively trimmed if it exceeds
            the profile's ``max_personality_tokens``.
        max_personality_tokens_override: When set to a positive value,
            overrides the profile's ``max_personality_tokens`` limit.
            Values ``<= 0`` are ignored (profile default is used).
        strategy_config: Strategy and trendslop mitigation config.
            When provided and the agent qualifies (C-suite/VP/Director
            or has explicit ``strategic_output_mode``), strategic
            analysis sections are injected into the prompt.
        async_task_state: Optional async task state channel.
            When non-empty, appends an ``Active Async Tasks``
            section to the prompt (survives trimming).

    Returns:
        Immutable :class:`SystemPrompt` with rendered content and metadata.

    Raises:
        PromptBuildError: If prompt construction fails.
    """
    validate_max_tokens(agent, max_tokens)
    validate_org_policies(agent, org_policies)

    if l1_summaries:
        logger.info(
            TOOL_L1_INJECTED,
            tool_count=len(l1_summaries),
            tool_names=tuple(s.name for s in l1_summaries),
        )

    profile = get_prompt_profile(model_tier)
    if max_personality_tokens_override is not None:
        if max_personality_tokens_override > 0:
            profile = profile.model_copy(
                update={"max_personality_tokens": max_personality_tokens_override},
            )
        else:
            logger.warning(
                PROMPT_PROFILE_SELECTED,
                override_ignored=max_personality_tokens_override,
                reason="max_personality_tokens_override must be > 0",
            )
    logger.info(
        PROMPT_PROFILE_SELECTED,
        requested_tier=model_tier,
        selected_tier=profile.tier,
        defaulted=model_tier is None,
        personality_mode=profile.personality_mode,
        autonomy_detail_level=profile.autonomy_detail_level,
    )

    # Advisory only -- issues are logged but never block prompt construction.
    if org_policies:
        try:
            validate_policy_quality(org_policies)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PROMPT_POLICY_VALIDATION_FAILED,
                agent_id=str(agent.id),
            )

    logger.info(
        PROMPT_BUILD_START,
        agent_id=str(agent.id),
        agent_name=agent.name,
        has_task=task is not None,
        tool_count=len(available_tools),
        has_company=company is not None,
        has_custom_template=custom_template is not None,
        model_tier=model_tier,
    )

    try:
        estimator = token_estimator or DefaultTokenEstimator()
        template_str = resolve_template(custom_template)

        result = render_with_trimming(
            template_str=template_str,
            agent=agent,
            role=role,
            task=task,
            available_tools=available_tools,
            l1_summaries=l1_summaries,
            company=company,
            org_policies=org_policies,
            max_tokens=max_tokens,
            estimator=estimator,
            effective_autonomy=effective_autonomy,
            context_budget_indicator=context_budget_indicator,
            currency=currency,
            profile=profile,
            trimming_enabled=personality_trimming_enabled,
            strategy_config=strategy_config,
        )
    except PromptBuildError:
        raise  # Already logged by inner functions.
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            PROMPT_BUILD_ERROR,
            agent_id=str(agent.id),
            agent_name=agent.name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        detail = sanitize_message(str(exc))
        msg = f"Unexpected error building prompt for agent '{agent.name}': {detail}"
        raise PromptBuildError(msg) from exc

    # Inject async task state section (survives trimming -- appended
    # after the main render since it must never be trimmed away).
    try:
        if async_task_state is not None and async_task_state.records:
            result = append_async_task_section(
                result,
                async_task_state,
                estimator,
            )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            PROMPT_BUILD_ERROR,
            agent_id=str(agent.id),
            agent_name=agent.name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        detail = sanitize_message(str(exc))
        msg = f"Error injecting async task state for agent '{agent.name}': {detail}"
        raise PromptBuildError(msg) from exc

    return log_and_return(agent, result)


def build_error_prompt(
    identity: AgentIdentity,
    agent_id: str,
    system_prompt: SystemPrompt | None,
) -> SystemPrompt:
    """Return the existing system prompt or a minimal error placeholder.

    Used by the engine when the execution pipeline fails and a
    ``SystemPrompt`` was never built (or was partially built).

    Args:
        identity: Agent identity for metadata.
        agent_id: String agent identifier.
        system_prompt: Previously built prompt, or ``None``.

    Returns:
        The existing prompt if available, else a minimal placeholder.
    """
    if system_prompt is not None:
        return system_prompt
    metadata = {**_build_metadata(identity), "agent_id": agent_id}
    return SystemPrompt(
        content="",
        template_version="error",
        estimated_tokens=0,
        sections=(),
        metadata=metadata,
    )
