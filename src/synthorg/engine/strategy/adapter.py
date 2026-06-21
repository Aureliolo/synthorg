"""Adapter bridging the strategy module into prompt construction.

Encapsulates strategy-specific imports, principle loading, and
error handling so that ``prompt.py`` delegates to a single call.
"""

from types import MappingProxyType

from synthorg.core.agent import AgentIdentity
from synthorg.engine.strategy.active_principle import ActivePrincipleProvider
from synthorg.engine.strategy.active_principle_provider import (
    current_active_principle_provider,
)
from synthorg.engine.strategy.models import ConstitutionalPrinciple, StrategyConfig
from synthorg.engine.strategy.principle_override_provider import (
    PrincipleOverrideProvider,
    current_principle_override_provider,
)
from synthorg.engine.strategy.principles import (
    StrategyPackNotFoundError,
    StrategyPackValidationError,
    load_and_merge,
)
from synthorg.engine.strategy.prompt_injection import (
    build_strategic_prompt_sections,
    should_inject_strategy,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.strategy import STRATEGY_PRINCIPLES_LOAD_FAILED

logger = get_logger(__name__)

_NULL_SECTIONS: MappingProxyType[str, object] = MappingProxyType(
    {
        "strategic_context": False,
        "strategic_context_text": None,
        "constitutional_principles_text": None,
        "contrarian_text": None,
        "confidence_text": None,
        "assumption_text": None,
        "output_instructions_text": None,
    }
)


def inject_strategy_context(
    context: dict[str, object],
    agent: AgentIdentity,
    strategy_config: StrategyConfig | None,
    *,
    active_principles: ActivePrincipleProvider | None = None,
    principle_overrides: PrincipleOverrideProvider | None = None,
) -> None:
    """Inject strategic analysis sections into template context.

    Sets ``strategic_context`` to ``True`` and populates the individual
    section text variables when the agent qualifies for strategic
    injection.  Otherwise sets ``strategic_context`` to ``False``
    and all section text variables to ``None``.

    When an ``active_principles`` provider is wired, durable constitutional
    principles applied by the self-improvement meta-loop are layered onto the
    pack + custom principles, filtered to those in scope for this agent's role
    and department. When a ``principle_overrides`` provider is wired, durable
    restored-principle text is overlaid onto matching principles by id.
    """
    if not should_inject_strategy(agent, strategy_config):
        context.update(_NULL_SECTIONS)
        return

    assert strategy_config is not None  # noqa: S101

    # An explicit provider (tests) wins; otherwise fall back to the ambient
    # provider the engine binds around the prompt build. Use an explicit
    # None-check so a falsey-but-present provider is still honoured.
    provider = (
        active_principles
        if active_principles is not None
        else current_active_principle_provider()
    )
    override_provider = (
        principle_overrides
        if principle_overrides is not None
        else current_principle_override_provider()
    )

    # Load principles if configured.
    principles: tuple[ConstitutionalPrinciple, ...] = ()
    try:
        principles = load_and_merge(
            strategy_config.constitutional_principles,
            active_principles=provider,
            principle_overrides=override_provider,
            role=agent.role,
            department=agent.department,
        )
    except (StrategyPackNotFoundError, StrategyPackValidationError) as exc:
        logger.warning(
            STRATEGY_PRINCIPLES_LOAD_FAILED,
            agent_id=str(agent.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    sections = build_strategic_prompt_sections(
        config=strategy_config,
        agent=agent,
        principles=principles,
    )

    context["strategic_context"] = True
    context.update(sections)
