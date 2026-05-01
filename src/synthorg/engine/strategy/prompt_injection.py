"""Strategic prompt section builder.

Builds the strategic analysis sections that are conditionally injected
into agent system prompts when strategy configuration is active.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import SeniorityLevel
from synthorg.engine.prompt_safety import (
    TAG_CONFIG_VALUE,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.engine.strategy.lenses import get_lens_definitions
from synthorg.engine.strategy.output import build_output_instructions
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.strategy import (
    STRATEGY_LENS_LOOKUP_FAILED,
    STRATEGY_PROMPT_INJECTED,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.strategy.models import (
        ConstitutionalPrinciple,
        StrategyConfig,
    )

logger = get_logger(__name__)

# Seniority levels that receive strategic prompt injection by default.
_STRATEGIC_LEVELS: frozenset[SeniorityLevel] = frozenset(
    {
        SeniorityLevel.C_SUITE,
        SeniorityLevel.VP,
        SeniorityLevel.DIRECTOR,
    }
)


def should_inject_strategy(
    agent: AgentIdentity,
    config: StrategyConfig | None,
) -> bool:
    """Determine whether strategic sections should be injected.

    Returns ``True`` if the agent has an explicit strategic output mode
    set, or if the agent's seniority level qualifies for strategic
    analysis.

    Args:
        agent: Agent identity.
        config: Strategy configuration (``None`` disables injection).

    Returns:
        Whether to inject strategic sections.
    """
    if config is None:
        return False
    if agent.strategic_output_mode is not None:
        return True
    return agent.level in _STRATEGIC_LEVELS


def build_strategic_prompt_sections(
    *,
    config: StrategyConfig,
    agent: AgentIdentity,
    principles: tuple[ConstitutionalPrinciple, ...] = (),
) -> dict[str, str | None]:
    """Build all strategic prompt sections.

    Returns a dict of section_name -> rendered text (or ``None`` if
    the section is not applicable).

    Args:
        config: Strategy configuration.
        agent: Agent identity.
        principles: Constitutional principles to inject.

    Returns:
        Dict mapping section names to rendered text or ``None``.
    """
    output_mode = agent.strategic_output_mode or config.output_mode

    # Strategic context section.
    # Phase 1: reads context directly from config fields. Phase 2 will
    # wire build_context() to support memory/composite context providers.
    # The context fields are admin-set but may carry per-tenant
    # content in a multi-tenant deployment, so we wrap each value
    # in a ``<config-value>`` fence and append a directive below
    # telling the model those fences contain data, not commands.
    industry = wrap_untrusted(TAG_CONFIG_VALUE, config.context.industry)
    maturity = wrap_untrusted(TAG_CONFIG_VALUE, config.context.maturity_stage)
    position = wrap_untrusted(
        TAG_CONFIG_VALUE,
        config.context.competitive_position,
    )
    context_text = (
        f"You operate in the industry {industry} at the stage "
        f"{maturity}. Competitive position: {position}.\n\n"
        + untrusted_content_directive((TAG_CONFIG_VALUE,))
    )

    # Constitutional principles section.
    principles_text: str | None = None
    if principles:
        lines = [
            "These anti-trendslop rules govern your strategic analysis. "
            "Violations of critical principles must be explicitly justified."
        ]
        for i, p in enumerate(principles, 1):
            # "warning" is the default severity -- omit the tag so only
            # non-default severities (critical, informational) are marked.
            severity_tag = (
                f" [{p.severity.value}]" if p.severity.value != "warning" else ""
            )
            lines.append(f"{i}. {p.text}{severity_tag}")
        principles_text = "\n".join(lines)

    # Contrarian analysis section.
    contrarian_text = (
        "For every recommendation, construct the strongest possible "
        "argument for the opposite approach. If you cannot articulate "
        "a compelling counter-argument, your analysis is incomplete."
    )

    # Confidence calibration section.
    confidence_text = (
        "When recommending a strategy, explicitly state:\n"
        "- Your confidence level (0-100%)\n"
        "- The confidence range (best case to worst case)\n"
        "- Key assumptions underlying the recommendation\n"
        "- What information would change your recommendation"
    )

    # Assumption surfacing section.
    assumption_text = (
        "Explicitly list the top 3-5 assumptions underlying each "
        "recommendation. For each assumption, state what would change "
        "if the assumption proved false."
    )

    # Output mode instructions.
    try:
        lens_definitions = get_lens_definitions(config.default_lenses)
    except KeyError as exc:
        logger.warning(
            STRATEGY_LENS_LOOKUP_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            configured_lenses=config.default_lenses,
        )
        lens_definitions = ()
    output_text = build_output_instructions(
        mode=output_mode,
        lenses=lens_definitions,
        agent=agent,
    )

    logger.debug(
        STRATEGY_PROMPT_INJECTED,
        agent_name=agent.name,
        output_mode=output_mode,
        principle_count=len(principles),
        lens_count=len(lens_definitions),
    )

    return {
        "strategic_context_text": context_text,
        "constitutional_principles_text": principles_text,
        "contrarian_text": contrarian_text,
        "confidence_text": confidence_text,
        "assumption_text": assumption_text,
        "output_instructions_text": output_text,
    }
