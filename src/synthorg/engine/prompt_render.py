"""Template-context assembly, rendering, and token-budget trimming.

The render engine behind :func:`synthorg.engine.prompt.build_system_prompt`:
assembles the Jinja2 context from the prompt inputs, renders and estimates
tokens, and progressively trims optional sections to fit a token budget.
Composes the result via :mod:`synthorg.engine.prompt_result`.
"""

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from synthorg.budget.currency import format_cost, get_currency_symbol
from synthorg.core.tool_disclosure import ToolL1Metadata
from synthorg.engine._prompt_helpers import SECTION_COMPANY as _SECTION_COMPANY
from synthorg.engine._prompt_helpers import (
    SECTION_ORG_POLICIES as _SECTION_ORG_POLICIES,
)
from synthorg.engine._prompt_helpers import TRIMMABLE_SECTIONS as _TRIMMABLE_SECTIONS
from synthorg.engine._prompt_helpers import build_core_context as _build_core_context
from synthorg.engine.prompt_inputs import PromptInputs
from synthorg.engine.prompt_result import SystemPrompt, build_prompt_result
from synthorg.engine.prompt_safety import (
    TAG_CONFIG_VALUE,
    wrap_untrusted,
)
from synthorg.engine.prompt_template import DEFAULT_TEMPLATE, WEB_RESEARCH_GUIDANCE
from synthorg.engine.prompt_validation import (
    log_trim_results,
    render_template,
)
from synthorg.engine.token_estimation import PromptTokenEstimator

if TYPE_CHECKING:
    from synthorg.engine.prompt_providers import PromptAmbientProviders


#: The two discovery tools the progressive-disclosure instruction names.
#: ``load_tool_resource`` is deliberately absent: the instruction never
#: mentions it, so a session holding the other two can follow it.
_DISCOVERY_INSTRUCTION_TOOLS: Final[frozenset[str]] = frozenset(
    {"list_tools", "load_tool"}
)

#: Tools whose presence makes the current-sources guidance actionable. Telling
#: a session to verify against upstream documentation it has no way to read
#: would be an instruction to fail.
_WEB_RESEARCH_TOOLS: Final[frozenset[str]] = frozenset({"web_search", "web_fetch"})


def _parameter_summary(summary: ToolL1Metadata) -> str:
    """Render a tool's parameter names for the catalogue line.

    Returns:
        Required names bare and optional ones in brackets, comma-joined;
        empty when the tool declares none.
    """
    names = [
        *summary.required_parameters,
        *(f"[{name}]" for name in summary.optional_parameters),
    ]
    return ", ".join(names)


def build_template_context(
    inputs: PromptInputs, *, providers: PromptAmbientProviders
) -> dict[str, object]:
    """Assemble the full Jinja2 template context from the prompt inputs.

    Args:
        inputs: What the prompt is rendered from.
        providers: The ambient provider snapshot, resolved once per prompt
            build. Required, because a default here would be a second place
            the ambient globals are read.

    Returns:
        The template variables dict.
    """
    agent = inputs.agent
    context = _build_core_context(
        agent, inputs.role, inputs.effective_autonomy, inputs.profile
    )

    context["currency_symbol"] = get_currency_symbol(inputs.currency)
    context["currency"] = inputs.currency
    budget_limit = agent.authority.budget_limit
    context["formatted_budget_limit"] = (
        format_cost(budget_limit, inputs.currency) if budget_limit > 0 else ""
    )
    # Org policies are operator-configured but injected verbatim into the
    # system prompt; fence each so a policy string cannot smuggle
    # instructions, and the appended directive treats the block as data.
    context["org_policies"] = tuple(
        wrap_untrusted(TAG_CONFIG_VALUE, policy) for policy in inputs.org_policies
    )
    context["context_budget"] = inputs.context_budget

    # Strategic analysis sections (conditional on config + agent eligibility).
    from synthorg.engine.strategy.adapter import (  # noqa: PLC0415
        inject_strategy_context,
    )

    inject_strategy_context(context, agent, inputs.strategy_config)

    # House-style directives (conditional on the ambient provider + agent scope)
    # and the standing ask directive (conditional on the ambient provider).
    from synthorg.engine.ask_policy.adapter import (  # noqa: PLC0415
        inject_ask_policy_context,
    )
    from synthorg.engine.output_style.adapter import (  # noqa: PLC0415
        inject_house_style_context,
    )

    inject_house_style_context(context, agent, provider=providers.house_style)
    inject_ask_policy_context(context, agent, provider=providers.ask_policy)

    available_tools = inputs.available_tools
    context["tools"] = (
        tuple({"name": t.name, "description": t.description} for t in available_tools)
        if available_tools
        else None
    )
    if inputs.l1_summaries:
        context["l1_tools"] = tuple(
            {
                "name": s.name,
                "short_description": s.short_description,
                "category": s.category,
                "cost_tier": s.typical_cost_tier,
                "parameters": _parameter_summary(s),
            }
            for s in inputs.l1_summaries
        )
        # Derived from the same registry view the section lists, never
        # assumed: a session whose registry holds only its own tools was
        # still told to call ``list_tools()`` first, and spent its turns
        # on tool-not-found before producing anything.
        context["has_tool_discovery"] = {
            s.name for s in inputs.l1_summaries
        } >= _DISCOVERY_INSTRUCTION_TOOLS
    else:
        context["l1_tools"] = None
        context["has_tool_discovery"] = False

    has_web_research = bool(available_tools) and any(
        t.name in _WEB_RESEARCH_TOOLS for t in available_tools
    )
    context["web_research"] = has_web_research
    context["web_research_section"] = (
        WEB_RESEARCH_GUIDANCE if has_web_research else None
    )

    if inputs.company is not None:
        context["company"] = {"name": inputs.company.name}
        context["company_departments"] = tuple(
            d.name for d in inputs.company.departments
        )
    else:
        context["company"] = None
        context["company_departments"] = None

    return context


def trim_sections(
    *,
    template_str: str,
    inputs: PromptInputs,
    max_tokens: int,
    estimator: PromptTokenEstimator,
    providers: PromptAmbientProviders,
) -> tuple[str, int, PromptInputs]:
    """Progressively remove optional sections until under token budget.

    Returns:
        ``(content, estimated, inputs)``: the final render and the inputs it
        was rendered from, which lack every section dropped to fit
        ``max_tokens`` so the result is assembled from what survived.
    """
    from synthorg.engine._prompt_helpers import (  # noqa: PLC0415
        SECTION_STRATEGY as _SECTION_STRATEGY_LOCAL,
    )

    trimmed_sections: list[str] = []

    for section in _TRIMMABLE_SECTIONS:
        content, estimated = render_and_estimate(
            template_str, inputs, estimator=estimator, providers=providers
        )
        if estimated <= max_tokens:
            break

        if section == _SECTION_STRATEGY_LOCAL and inputs.strategy_config is not None:
            inputs = replace(inputs, strategy_config=None)
        elif section == _SECTION_COMPANY and inputs.company is not None:
            inputs = replace(inputs, company=None)
        elif (
            section == _SECTION_ORG_POLICIES
            and inputs.org_policies
            and (inputs.profile is None or inputs.profile.include_org_policies)
        ):
            inputs = replace(inputs, org_policies=())
        else:
            continue

        trimmed_sections.append(section)
    else:
        # All sections exhausted -- do a final render.
        content, estimated = render_and_estimate(
            template_str, inputs, estimator=estimator, providers=providers
        )

    log_trim_results(inputs.agent, max_tokens, estimated, trimmed_sections)

    return content, estimated, inputs


def render_with_trimming(
    *,
    template_str: str,
    inputs: PromptInputs,
    max_tokens: int | None,
    estimator: PromptTokenEstimator,
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

    providers = current_prompt_providers()

    content, estimated = render_and_estimate(
        template_str, inputs, estimator=estimator, providers=providers
    )

    if max_tokens is not None and estimated > max_tokens:
        content, estimated, inputs = trim_sections(
            template_str=template_str,
            inputs=inputs,
            max_tokens=max_tokens,
            estimator=estimator,
            providers=providers,
        )

    return build_prompt_result(
        content,
        estimated,
        inputs=inputs,
        custom_template=template_str is not DEFAULT_TEMPLATE,
        providers=providers,
    )


def render_and_estimate(
    template_str: str,
    inputs: PromptInputs,
    *,
    estimator: PromptTokenEstimator,
    providers: PromptAmbientProviders,
) -> tuple[str, int]:
    """Render the template and estimate its token count.

    Args:
        template_str: Jinja2 template text.
        inputs: What the prompt is rendered from.
        estimator: Token estimator.
        providers: The ambient provider snapshot, resolved once per prompt
            build.

    Returns:
        Tuple of (rendered content, estimated token count).
    """
    context = build_template_context(inputs, providers=providers)
    content = render_template(template_str, context)
    return content, estimator.estimate_tokens(content)
