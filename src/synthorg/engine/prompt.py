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

from typing import TYPE_CHECKING, Any

from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.currency import (
    DEFAULT_CURRENCY,
    CurrencyCode,
    format_cost,
    get_currency_symbol,
)
from synthorg.communication.async_tasks.models import (  # noqa: TC001
    AsyncTaskStateChannel,
)
from synthorg.engine._prompt_helpers import SECTION_COMPANY as _SECTION_COMPANY
from synthorg.engine._prompt_helpers import (
    SECTION_ORG_POLICIES as _SECTION_ORG_POLICIES,
)
from synthorg.engine._prompt_helpers import SECTION_TASK as _SECTION_TASK
from synthorg.engine._prompt_helpers import TRIMMABLE_SECTIONS as _TRIMMABLE_SECTIONS
from synthorg.engine._prompt_helpers import PersonalityTrimInfo
from synthorg.engine._prompt_helpers import build_core_context as _build_core_context
from synthorg.engine._prompt_helpers import build_metadata as _build_metadata
from synthorg.engine._prompt_helpers import compute_sections as _compute_sections
from synthorg.engine.errors import PromptBuildError
from synthorg.engine.policy_validation import validate_policy_quality
from synthorg.engine.prompt_profiles import PromptProfile, get_prompt_profile
from synthorg.engine.prompt_template import (
    DEFAULT_TEMPLATE,
    PROMPT_TEMPLATE_VERSION,
)
from synthorg.engine.prompt_validation import (
    inject_async_task_section,
    log_prompt_build_success,
    log_trim_results,
    render_template,
    resolve_template,
    validate_max_tokens,
    validate_org_policies,
)
from synthorg.engine.sanitization import sanitize_message
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

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.core.company import Company
    from synthorg.core.role import Role
    from synthorg.core.task import Task
    from synthorg.core.tool_disclosure import ToolL1Metadata
    from synthorg.core.types import ModelTier
    from synthorg.engine.strategy.models import StrategyConfig
    from synthorg.providers.models import ToolDefinition
    from synthorg.security.autonomy.models import EffectiveAutonomy

logger = get_logger(__name__)

# Sandboxed to prevent arbitrary code execution in user-provided custom templates.
# Thread-safe for concurrent parse/render calls. Do NOT add filters, globals,
# or extensions after module initialization.
_SANDBOX_ENV = SandboxedEnvironment()


# ── Result model ─────────────────────────────────────────────────


class SystemPrompt(BaseModel):
    """Immutable result of system prompt construction.

    Attributes:
        content: Full rendered prompt text.
        template_version: Version of the template that produced this prompt.
        estimated_tokens: Token estimate of the prompt content.
        sections: Names of sections included in the prompt.
        metadata: Agent identity metadata (agent_id, name, role,
            department, level, and optionally profile_tier).
        personality_trim_info: Populated when personality section was
            trimmed to fit the profile's token budget.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    content: str = Field(description="Full rendered prompt text")
    template_version: str = Field(
        description="Template version that produced this prompt",
    )
    estimated_tokens: int = Field(
        ge=0,
        description="Estimated token count of prompt content",
    )
    sections: tuple[str, ...] = Field(
        description="Names of sections included in the prompt",
    )
    metadata: dict[str, str] = Field(
        description="Agent identity metadata (string-only values; shallow-frozen)",
    )
    personality_trim_info: PersonalityTrimInfo | None = Field(
        default=None,
        description="Populated when personality section was trimmed",
    )


# ── Public API ───────────────────────────────────────────────────


def build_system_prompt(  # noqa: PLR0913, C901, PLR0912
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
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(
                PROMPT_POLICY_VALIDATION_FAILED,
                agent_id=str(agent.id),
                exc_info=True,
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

        result = _render_with_trimming(
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
    except MemoryError, RecursionError:
        logger.error(
            PROMPT_BUILD_ERROR,
            agent_id=str(agent.id),
            agent_name=agent.name,
            error="non-recoverable error building prompt",
            exc_info=True,
        )
        raise
    except Exception as exc:
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
            result = _inject_async_task_section(
                result,
                async_task_state,
                estimator,
            )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
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

    return _log_and_return(agent, result)


def _inject_async_task_section(
    prompt: SystemPrompt,
    state: AsyncTaskStateChannel,
    estimator: PromptTokenEstimator,
) -> SystemPrompt:
    """Append an async task state section to a rendered prompt.

    This section is appended after trimming so it is never trimmed away.
    Recomputes ``estimated_tokens`` to reflect the injected content.
    """
    new_content, new_tokens = inject_async_task_section(
        content=prompt.content,
        state=state,
        estimator=estimator,
    )
    return prompt.model_copy(
        update={
            "content": new_content,
            "estimated_tokens": new_tokens,
            "sections": (*prompt.sections, "async_tasks"),
        },
    )


def _log_and_return(
    agent: AgentIdentity,
    result: SystemPrompt,
) -> SystemPrompt:
    """Log prompt build success and return the result."""
    log_prompt_build_success(
        agent,
        sections=result.sections,
        estimated_tokens=result.estimated_tokens,
        template_version=result.template_version,
    )
    return result


# ── Private helpers ──────────────────────────────────────────────


def _build_template_context(  # noqa: PLR0913
    *,
    agent: AgentIdentity,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    l1_summaries: tuple[ToolL1Metadata, ...] = (),
    company: Company | None,
    org_policies: tuple[str, ...] = (),
    effective_autonomy: EffectiveAutonomy | None = None,
    context_budget: str | None = None,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    profile: PromptProfile | None = None,
    trimming_enabled: bool = True,
    estimator: PromptTokenEstimator | None = None,
    strategy_config: StrategyConfig | None = None,
) -> tuple[dict[str, Any], PersonalityTrimInfo | None]:
    """Assemble the full Jinja2 template context from agent and optional inputs.

    Args:
        agent: Agent identity.
        role: Optional role with description.
        task: Optional task context.
        available_tools: Tool definitions.
        l1_summaries: L1 metadata for system prompt injection.
        company: Optional company context.
        org_policies: Company-wide policy texts.
        effective_autonomy: Resolved autonomy for the current run.
        context_budget: Formatted context budget indicator string.
        currency: ISO 4217 currency code for budget displays.
        profile: Prompt profile controlling rendering verbosity.
        trimming_enabled: Whether personality trimming is active.
        estimator: Token estimator for personality trimming.
        strategy_config: Strategy config for trendslop mitigation.

    Returns:
        Tuple of (template variables dict, personality trim info or None).
    """
    context, trim_info = _build_core_context(
        agent,
        role,
        effective_autonomy,
        profile,
        trimming_enabled=trimming_enabled,
        estimator=estimator,
    )

    context["currency_symbol"] = get_currency_symbol(currency)
    context["currency"] = currency
    budget_limit = agent.authority.budget_limit
    context["formatted_budget_limit"] = (
        format_cost(budget_limit, currency) if budget_limit > 0 else ""
    )
    context["org_policies"] = org_policies
    context["context_budget"] = context_budget

    # Strategic analysis sections (conditional on config + agent eligibility).
    from synthorg.engine.strategy.adapter import (  # noqa: PLC0415
        inject_strategy_context,
    )

    inject_strategy_context(context, agent, strategy_config)

    if task is not None:
        context["task"] = {
            "title": task.title,
            "description": task.description,
            "acceptance_criteria": tuple(
                {"description": c.description} for c in task.acceptance_criteria
            ),
            "budget_limit": task.budget_limit,
            "deadline": task.deadline,
        }
        context["formatted_task_budget"] = (
            format_cost(task.budget_limit, currency) if task.budget_limit > 0 else ""
        )
    else:
        context["task"] = None
        context["formatted_task_budget"] = ""

    context["tools"] = (
        tuple({"name": t.name, "description": t.description} for t in available_tools)
        if available_tools
        else None
    )
    if l1_summaries:
        context["l1_tools"] = tuple(
            {
                "name": s.name,
                "short_description": s.short_description,
                "category": s.category,
                "cost_tier": s.typical_cost_tier,
            }
            for s in l1_summaries
        )
    else:
        context["l1_tools"] = None

    if company is not None:
        context["company"] = {"name": company.name}
        context["company_departments"] = tuple(d.name for d in company.departments)
    else:
        context["company"] = None
        context["company_departments"] = None

    return context, trim_info


def _trim_sections(  # noqa: PLR0913
    *,
    template_str: str,
    agent: AgentIdentity,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    l1_summaries: tuple[ToolL1Metadata, ...],
    company: Company | None,
    org_policies: tuple[str, ...],
    max_tokens: int,
    estimator: PromptTokenEstimator,
    effective_autonomy: EffectiveAutonomy | None = None,
    context_budget: str | None = None,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    profile: PromptProfile | None = None,
    trimming_enabled: bool = True,
    strategy_config: StrategyConfig | None = None,
) -> tuple[
    str,
    int,
    Task | None,
    Company | None,
    tuple[str, ...],
    StrategyConfig | None,
]:
    """Progressively remove optional sections until under token budget.

    Returns ``(content, estimated, task, company, org_policies,
    strategy_config)`` so the caller can reuse the final render.
    """
    from synthorg.engine._prompt_helpers import (  # noqa: PLC0415
        SECTION_STRATEGY as _SECTION_STRATEGY_LOCAL,
    )

    trimmed_sections: list[str] = []

    for section in _TRIMMABLE_SECTIONS:
        content, estimated, _ = _render_and_estimate(
            template_str,
            agent,
            role,
            task,
            available_tools,
            l1_summaries,
            company,
            org_policies,
            estimator,
            effective_autonomy=effective_autonomy,
            context_budget=context_budget,
            currency=currency,
            profile=profile,
            trimming_enabled=trimming_enabled,
            strategy_config=strategy_config,
        )
        if estimated <= max_tokens:
            break

        if section == _SECTION_STRATEGY_LOCAL and strategy_config is not None:
            strategy_config = None
        elif section == _SECTION_COMPANY and company is not None:
            company = None
        elif (
            section == _SECTION_ORG_POLICIES
            and org_policies
            and (profile is None or profile.include_org_policies)
        ):
            org_policies = ()
        elif section == _SECTION_TASK and task is not None:
            task = None
        else:
            continue

        trimmed_sections.append(section)
    else:
        # All sections exhausted -- do a final render.
        content, estimated, _ = _render_and_estimate(
            template_str,
            agent,
            role,
            task,
            available_tools,
            l1_summaries,
            company,
            org_policies,
            estimator,
            effective_autonomy=effective_autonomy,
            context_budget=context_budget,
            currency=currency,
            profile=profile,
            trimming_enabled=trimming_enabled,
            strategy_config=strategy_config,
        )

    log_trim_results(agent, max_tokens, estimated, trimmed_sections)

    return content, estimated, task, company, org_policies, strategy_config


def _render_with_trimming(  # noqa: PLR0913
    *,
    template_str: str,
    agent: AgentIdentity,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    l1_summaries: tuple[ToolL1Metadata, ...] = (),
    company: Company | None,
    org_policies: tuple[str, ...] = (),
    max_tokens: int | None,
    estimator: PromptTokenEstimator,
    effective_autonomy: EffectiveAutonomy | None = None,
    context_budget_indicator: str | None = None,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    profile: PromptProfile | None = None,
    trimming_enabled: bool = True,
    strategy_config: StrategyConfig | None = None,
) -> SystemPrompt:
    """Render the prompt, trimming optional sections if over token budget."""
    content, estimated, trim_info = _render_and_estimate(
        template_str,
        agent,
        role,
        task,
        available_tools,
        l1_summaries,
        company,
        org_policies,
        estimator,
        effective_autonomy=effective_autonomy,
        context_budget=context_budget_indicator,
        currency=currency,
        profile=profile,
        trimming_enabled=trimming_enabled,
        strategy_config=strategy_config,
    )

    if max_tokens is not None and estimated > max_tokens:
        content, estimated, task, company, org_policies, strategy_config = (
            _trim_sections(
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
                context_budget=context_budget_indicator,
                currency=currency,
                profile=profile,
                trimming_enabled=trimming_enabled,
                strategy_config=strategy_config,
            )
        )

    return _build_prompt_result(
        content,
        estimated,
        task,
        available_tools,
        company,
        org_policies,
        agent,
        custom_template=template_str is not DEFAULT_TEMPLATE,
        context_budget=context_budget_indicator,
        profile=profile,
        personality_trim_info=trim_info,
        strategy_config=strategy_config,
    )


def _build_prompt_result(  # noqa: PLR0913
    content: str,
    estimated: int,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    company: Company | None,
    org_policies: tuple[str, ...],
    agent: AgentIdentity,
    *,
    custom_template: bool = False,
    context_budget: str | None = None,
    profile: PromptProfile | None = None,
    personality_trim_info: PersonalityTrimInfo | None = None,
    strategy_config: StrategyConfig | None = None,
) -> SystemPrompt:
    """Assemble the final ``SystemPrompt`` from rendered content."""
    from synthorg.engine.strategy.prompt_injection import (  # noqa: PLC0415
        should_inject_strategy,
    )

    sections = _compute_sections(
        task=task,
        available_tools=available_tools,
        company=company,
        org_policies=org_policies,
        custom_template=custom_template,
        context_budget=context_budget,
        profile=profile,
        has_strategy=should_inject_strategy(agent, strategy_config),
    )
    metadata = _build_metadata(agent)
    if profile is not None:
        metadata["profile_tier"] = profile.tier
    return SystemPrompt(
        content=content,
        template_version=PROMPT_TEMPLATE_VERSION,
        estimated_tokens=estimated,
        sections=sections,
        metadata=metadata,
        personality_trim_info=personality_trim_info,
    )


def _render_and_estimate(  # noqa: PLR0913
    template_str: str,
    agent: AgentIdentity,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    l1_summaries: tuple[ToolL1Metadata, ...],
    company: Company | None,
    org_policies: tuple[str, ...],
    estimator: PromptTokenEstimator,
    *,
    effective_autonomy: EffectiveAutonomy | None = None,
    context_budget: str | None = None,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    profile: PromptProfile | None = None,
    trimming_enabled: bool = True,
    strategy_config: StrategyConfig | None = None,
) -> tuple[str, int, PersonalityTrimInfo | None]:
    """Render the template and estimate its token count.

    Args:
        template_str: Jinja2 template text.
        agent: Agent identity.
        role: Optional role.
        task: Optional task context.
        available_tools: Tool definitions.
        l1_summaries: L1 metadata for system prompt injection.
        company: Optional company context.
        org_policies: Company-wide policy texts.
        estimator: Token estimator.
        effective_autonomy: Resolved autonomy for the current run.
        context_budget: Formatted context budget indicator string.
        currency: ISO 4217 currency code for budget displays.
        profile: Prompt profile controlling rendering verbosity.
        trimming_enabled: Whether personality trimming is active.
        strategy_config: Strategy config for trendslop mitigation.

    Returns:
        Tuple of (rendered content, estimated token count,
        personality trim info or None).
    """
    context, trim_info = _build_template_context(
        agent=agent,
        role=role,
        task=task,
        available_tools=available_tools,
        l1_summaries=l1_summaries,
        company=company,
        org_policies=org_policies,
        effective_autonomy=effective_autonomy,
        context_budget=context_budget,
        currency=currency,
        profile=profile,
        trimming_enabled=trimming_enabled,
        estimator=estimator,
        strategy_config=strategy_config,
    )
    content = render_template(template_str, context)
    return content, estimator.estimate_tokens(content), trim_info


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
