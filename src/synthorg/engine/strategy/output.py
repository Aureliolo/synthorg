"""Strategic output mode handler.

Generates output-mode-specific prompt instructions that shape how
strategic agents frame their recommendations.
"""

import copy
from types import MappingProxyType
from typing import Final

from synthorg.core.agent import AgentIdentity
from synthorg.core.authority import role_depth
from synthorg.engine.strategy.lenses import LensDefinition
from synthorg.hr.strategy_mode import StrategicOutputMode
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import STRATEGY_OUTPUT_HANDLED

logger = get_logger(__name__)

# Executive tier by reporting depth: the CEO (depth 0) and the C-suite
# reporting directly to it (depth 1) act as decision makers; everyone
# deeper defaults to an advisory posture.
_DECISION_MAKER_MAX_DEPTH: Final[int] = 1


def build_output_instructions(
    *,
    mode: StrategicOutputMode,
    lenses: tuple[LensDefinition, ...],
    agent: AgentIdentity | None = None,
) -> str:
    """Generate output-mode-specific prompt instructions.

    For ``context_dependent`` mode, resolves to ``decision_maker`` for
    C-suite/VP agents, ``advisor`` for all others.

    Args:
        mode: Strategic output mode.
        lenses: Active lens definitions for this evaluation.
        agent: Agent identity for context-dependent resolution.

    Returns:
        Prompt instruction text.
    """
    resolved_mode = _resolve_mode(mode, agent)

    instructions = _MODE_INSTRUCTIONS[resolved_mode]

    lens_text = _format_lens_instructions(lenses)
    if lens_text:
        instructions = f"{instructions}\n\n{lens_text}"

    logger.debug(
        STRATEGY_OUTPUT_HANDLED,
        original_mode=mode,
        resolved_mode=resolved_mode,
        lens_count=len(lenses),
    )

    return instructions


def _resolve_mode(
    mode: StrategicOutputMode,
    agent: AgentIdentity | None,
) -> StrategicOutputMode:
    """Resolve context_dependent mode to a concrete mode.

    Returns:
        ``mode`` unchanged when it is already concrete; otherwise
        ``DECISION_MAKER`` for executive-tier agents (shallow reporting
        depth) and ``ADVISOR`` for everyone else.
    """
    if mode != StrategicOutputMode.CONTEXT_DEPENDENT:
        return mode

    if agent is not None and role_depth(agent.role) <= _DECISION_MAKER_MAX_DEPTH:
        return StrategicOutputMode.DECISION_MAKER

    return StrategicOutputMode.ADVISOR


def _format_lens_instructions(
    lenses: tuple[LensDefinition, ...],
) -> str:
    """Format active lenses into evaluation instructions.

    Returns:
        A newline-joined block of lens-evaluation instructions;
        empty string when no lenses are active.
    """
    if not lenses:
        return ""

    parts = ["Evaluate all options through these lenses:"]
    parts.extend(f"- **{lens.name}**: {lens.prompt_fragment}" for lens in lenses)

    return "\n".join(parts)


_MODE_INSTRUCTIONS: MappingProxyType[StrategicOutputMode, str] = MappingProxyType(
    copy.deepcopy(
        {
            StrategicOutputMode.OPTION_EXPANDER: (
                "Present ALL viable options with analysis through each strategic "
                "lens. Do not rank or recommend -- lay out the full option space "
                "with trade-offs for each. Include at least one unconventional "
                "option and explicitly evaluate the status quo."
            ),
            StrategicOutputMode.ADVISOR: (
                "Recommend the top 2-3 options with reasoning and caveats for "
                "each. Clearly state your top recommendation but present it as "
                "advice, not a decision. Include the key assumptions underlying "
                "each recommendation and what would change your advice."
            ),
            StrategicOutputMode.DECISION_MAKER: (
                "Make a final recommendation with full justification. State your "
                "decision clearly, then provide the reasoning. Include a risk "
                "assessment, confidence level, key assumptions, and what "
                "conditions would warrant revisiting this decision."
            ),
        }
    )
)
