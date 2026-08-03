"""Template-context assembly, rendering, and token-budget trimming.

The render engine behind :func:`synthorg.engine.prompt.build_system_prompt`:
assembles the Jinja2 context from agent + optional inputs, renders and
estimates tokens, and progressively trims optional sections to fit a
token budget. Composes the result via :mod:`synthorg.engine.prompt_result`.
"""

from typing import TYPE_CHECKING

from synthorg.budget.currency import (
    DEFAULT_CURRENCY,
    CurrencyCode,
    format_cost,
    get_currency_symbol,
)
from synthorg.core.agent import AgentIdentity
from synthorg.core.role import Role
from synthorg.core.task import Task
from synthorg.core.tool_disclosure import ToolL1Metadata
from synthorg.engine._prompt_helpers import SECTION_COMPANY as _SECTION_COMPANY
from synthorg.engine._prompt_helpers import (
    SECTION_ORG_POLICIES as _SECTION_ORG_POLICIES,
)
from synthorg.engine._prompt_helpers import SECTION_TASK as _SECTION_TASK
from synthorg.engine._prompt_helpers import TRIMMABLE_SECTIONS as _TRIMMABLE_SECTIONS
from synthorg.engine._prompt_helpers import PersonalityTrimInfo
from synthorg.engine._prompt_helpers import build_core_context as _build_core_context
from synthorg.engine.prompt_profiles import PromptProfile
from synthorg.engine.prompt_result import SystemPrompt, build_prompt_result
from synthorg.engine.prompt_safety import (
    TAG_CONFIG_VALUE,
    TAG_TASK_DATA,
    wrap_untrusted,
)
from synthorg.engine.prompt_template import DEFAULT_TEMPLATE
from synthorg.engine.prompt_validation import (
    log_trim_results,
    render_template,
)
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.engine.token_estimation import PromptTokenEstimator
from synthorg.providers.models import ToolDefinition

if TYPE_CHECKING:
    from synthorg.core.company import Company
    from synthorg.core.effective_autonomy import EffectiveAutonomy
    from synthorg.engine.prompt_providers import PromptAmbientProviders


def build_template_context(  # noqa: PLR0913
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
    prompt_providers: PromptAmbientProviders,
) -> tuple[dict[str, object], PersonalityTrimInfo | None]:
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
        prompt_providers: The ambient provider snapshot, resolved once per
            prompt build. Required, because a default here would be a second
            place the ambient globals are read.

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
    # Org policies are operator-configured but injected verbatim into the
    # system prompt; fence each so a policy string cannot smuggle
    # instructions, and the appended directive treats the block as data.
    context["org_policies"] = tuple(
        wrap_untrusted(TAG_CONFIG_VALUE, policy) for policy in org_policies
    )
    context["context_budget"] = context_budget

    # Strategic analysis sections (conditional on config + agent eligibility).
    from synthorg.engine.strategy.adapter import (  # noqa: PLC0415
        inject_strategy_context,
    )

    inject_strategy_context(context, agent, strategy_config)

    # House-style directives (conditional on the ambient provider + agent scope)
    # and the standing ask directive (conditional on the ambient provider).
    from synthorg.engine.ask_policy.adapter import (  # noqa: PLC0415
        inject_ask_policy_context,
    )
    from synthorg.engine.output_style.adapter import (  # noqa: PLC0415
        inject_house_style_context,
    )

    inject_house_style_context(context, agent, provider=prompt_providers.house_style)
    inject_ask_policy_context(context, agent, provider=prompt_providers.ask_policy)

    if task is not None:
        # Title / description / acceptance criteria are client-supplied
        # free text injected into the system prompt; fence each with
        # TAG_TASK_DATA so an injected ``</task-data>`` cannot break out
        # and the appended directive marks the block as data the model
        # must not obey as instructions.
        context["task"] = {
            "title": wrap_untrusted(TAG_TASK_DATA, task.title),
            "description": wrap_untrusted(TAG_TASK_DATA, task.description),
            "acceptance_criteria": tuple(
                {"description": wrap_untrusted(TAG_TASK_DATA, c.description)}
                for c in task.acceptance_criteria
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


def trim_sections(  # noqa: PLR0913
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
    prompt_providers: PromptAmbientProviders,
) -> tuple[
    str,
    int,
    Task | None,
    Company | None,
    tuple[str, ...],
    StrategyConfig | None,
]:
    """Progressively remove optional sections until under token budget.

    Returns:
        ``(content, estimated, task, company, org_policies,
        strategy_config)`` so the caller can reuse the final render
        (each section may be dropped to fit ``max_tokens``).
    """
    from synthorg.engine._prompt_helpers import (  # noqa: PLC0415
        SECTION_STRATEGY as _SECTION_STRATEGY_LOCAL,
    )

    trimmed_sections: list[str] = []

    for section in _TRIMMABLE_SECTIONS:
        content, estimated, _ = render_and_estimate(
            template_str,
            agent,
            role=role,
            task=task,
            available_tools=available_tools,
            l1_summaries=l1_summaries,
            company=company,
            org_policies=org_policies,
            estimator=estimator,
            effective_autonomy=effective_autonomy,
            context_budget=context_budget,
            currency=currency,
            profile=profile,
            trimming_enabled=trimming_enabled,
            strategy_config=strategy_config,
            prompt_providers=prompt_providers,
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
        content, estimated, _ = render_and_estimate(
            template_str,
            agent,
            role=role,
            task=task,
            available_tools=available_tools,
            l1_summaries=l1_summaries,
            company=company,
            org_policies=org_policies,
            estimator=estimator,
            effective_autonomy=effective_autonomy,
            context_budget=context_budget,
            currency=currency,
            profile=profile,
            trimming_enabled=trimming_enabled,
            strategy_config=strategy_config,
            prompt_providers=prompt_providers,
        )

    log_trim_results(agent, max_tokens, estimated, trimmed_sections)

    return content, estimated, task, company, org_policies, strategy_config


def render_with_trimming(  # noqa: PLR0913
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
    """Render the prompt, trimming optional sections if over token budget.

    Returns:
        The rendered :class:`SystemPrompt` (trimmed sections recorded
        in ``trimmed_sections`` when the initial render was over
        budget).
    """
    # Snapshot the ambient providers ONCE so the injected sections and the
    # sections manifest agree even if an operator hot-swaps a pack or the ask
    # policy mid-build; every downstream read uses this same immutable snapshot.
    from synthorg.engine.prompt_providers import (  # noqa: PLC0415
        current_prompt_providers,
    )

    prompt_providers = current_prompt_providers()

    content, estimated, trim_info = render_and_estimate(
        template_str,
        agent,
        role=role,
        task=task,
        available_tools=available_tools,
        l1_summaries=l1_summaries,
        company=company,
        org_policies=org_policies,
        estimator=estimator,
        effective_autonomy=effective_autonomy,
        context_budget=context_budget_indicator,
        currency=currency,
        profile=profile,
        trimming_enabled=trimming_enabled,
        strategy_config=strategy_config,
        prompt_providers=prompt_providers,
    )

    if max_tokens is not None and estimated > max_tokens:
        content, estimated, task, company, org_policies, strategy_config = (
            trim_sections(
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
                prompt_providers=prompt_providers,
            )
        )

    return build_prompt_result(
        content,
        estimated,
        task=task,
        available_tools=available_tools,
        company=company,
        org_policies=org_policies,
        agent=agent,
        custom_template=template_str is not DEFAULT_TEMPLATE,
        context_budget=context_budget_indicator,
        profile=profile,
        personality_trim_info=trim_info,
        strategy_config=strategy_config,
        prompt_providers=prompt_providers,
    )


def render_and_estimate(  # noqa: PLR0913
    template_str: str,
    agent: AgentIdentity,
    *,
    role: Role | None,
    task: Task | None,
    available_tools: tuple[ToolDefinition, ...],
    l1_summaries: tuple[ToolL1Metadata, ...],
    company: Company | None,
    org_policies: tuple[str, ...],
    estimator: PromptTokenEstimator,
    effective_autonomy: EffectiveAutonomy | None = None,
    context_budget: str | None = None,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    profile: PromptProfile | None = None,
    trimming_enabled: bool = True,
    strategy_config: StrategyConfig | None = None,
    prompt_providers: PromptAmbientProviders,
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
        prompt_providers: The ambient provider snapshot, resolved once per
            prompt build.

    Returns:
        Tuple of (rendered content, estimated token count,
        personality trim info or None).
    """
    context, trim_info = build_template_context(
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
        prompt_providers=prompt_providers,
    )
    content = render_template(template_str, context)
    return content, estimator.estimate_tokens(content), trim_info
