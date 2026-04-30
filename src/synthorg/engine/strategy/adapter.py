"""Adapter bridging the strategy module into prompt construction.

Encapsulates strategy-specific imports, principle loading, and
error handling so that ``prompt.py`` delegates to a single call.
"""

from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.strategy.models import ConstitutionalPrinciple, StrategyConfig

logger = get_logger(__name__)

_NULL_SECTIONS: dict[str, object] = {
    "strategic_context": False,
    "strategic_context_text": None,
    "constitutional_principles_text": None,
    "contrarian_text": None,
    "confidence_text": None,
    "assumption_text": None,
    "output_instructions_text": None,
}


def inject_strategy_context(
    context: dict[str, Any],
    agent: AgentIdentity,
    strategy_config: StrategyConfig | None,
) -> None:
    """Inject strategic analysis sections into template context.

    Sets ``strategic_context`` to ``True`` and populates the individual
    section text variables when the agent qualifies for strategic
    injection.  Otherwise sets ``strategic_context`` to ``False``
    and all section text variables to ``None``.
    """
    if not should_inject_strategy(agent, strategy_config):
        context.update(_NULL_SECTIONS)
        return

    assert strategy_config is not None  # noqa: S101

    # Load principles if configured.
    principles: tuple[ConstitutionalPrinciple, ...] = ()
    try:
        principles = load_and_merge(
            strategy_config.constitutional_principles,
        )
    except MemoryError, RecursionError:
        raise
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
