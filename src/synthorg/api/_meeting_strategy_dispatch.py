# module-kind: code
"""Strategy-subsystem dispatch hooks for the meeting stack.

Binds the ``engine.strategy`` premortem / consensus-velocity /
progressive-tier subsystems behind the structural hook signatures the
``communication.meeting`` package exposes, so the meeting package never
imports ``engine.strategy`` (which would close an import cycle: the
strategy package already depends on the meeting package).
"""

from collections.abc import Callable
from typing import Final

from synthorg.communication.meeting.protocol import AgentCaller
from synthorg.communication.meeting.structured_phases import (
    ConsensusVelocityHook,
    PremortemHook,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.strategy.consensus import ConsensusVelocityDetector
from synthorg.engine.strategy.models import (
    CostTierPreset,
    StrategyConfig,
)
from synthorg.engine.strategy.premortem import (
    DefaultPremortemExecutor,
    PremortemOutput,
)
from synthorg.engine.strategy.tiers import ProgressiveTierResolver

# Progressive-tier token-budget multipliers. A more impactful analysis
# tier earns a deeper token allowance; a minimal tier trims it. Held as
# named constants (not bare dict literals) so the no-magic-numbers gate
# sees intentional, documented values.
_MINIMAL_BUDGET_MULTIPLIER: Final[float] = 0.5
_MODERATE_BUDGET_MULTIPLIER: Final[float] = 1.0
_GENEROUS_BUDGET_MULTIPLIER: Final[float] = 2.0

_TIER_BUDGET_MULTIPLIERS: Final[dict[CostTierPreset, float]] = {
    CostTierPreset.MINIMAL: _MINIMAL_BUDGET_MULTIPLIER,
    CostTierPreset.MODERATE: _MODERATE_BUDGET_MULTIPLIER,
    CostTierPreset.GENEROUS: _GENEROUS_BUDGET_MULTIPLIER,
}


def build_consensus_hook(config: StrategyConfig) -> ConsensusVelocityHook:
    """Bind a consensus-velocity detector behind the meeting hook.

    Returns:
        A callable that reports whether a tuple of input positions has
        prematurely converged, per ``config.consensus_velocity``.
    """
    detector = ConsensusVelocityDetector()
    velocity_config = config.consensus_velocity

    def _hook(positions: tuple[str, ...]) -> bool:
        return detector.detect(positions, velocity_config).detected

    return _hook


def build_premortem_hook(config: StrategyConfig) -> PremortemHook:
    """Bind a premortem executor behind the meeting hook.

    Returns:
        A coroutine callable that runs premortem over a synthesis
        summary and renders the result as a markdown section (empty
        string when nothing surfaced), per ``config.premortem``.
    """
    executor = DefaultPremortemExecutor()
    premortem_config = config.premortem

    async def _hook(
        *,
        synthesis_text: str,
        participant_ids: tuple[str, ...],
        agent_caller: AgentCaller,
        token_budget: int,
        context_id: str,
    ) -> str:
        output = await executor.execute(
            synthesis_text=synthesis_text,
            participant_ids=tuple(NotBlankStr(pid) for pid in participant_ids),
            agent_caller=agent_caller,
            config=premortem_config,
            token_budget=token_budget,
            context_id=context_id,
        )
        return _render_premortem(output)

    return _hook


def build_budget_scaler(config: StrategyConfig) -> Callable[[int], int]:
    """Bind a progressive-tier token-budget scaler.

    With no impact signal at scheduling time the resolver returns the
    configured static ``cost_tier``; this still applies the tier's
    budget multiplier so the depth preset shapes the meeting's token
    allowance instead of being inert. When an impact feed is wired
    later, the same scaler scales by the resolved tier with no change
    here.

    Returns:
        A callable mapping a base token budget to its tier-adjusted
        value (always >= 1).
    """
    resolver = ProgressiveTierResolver()

    def _scale(base_tokens: int) -> int:
        tier = resolver.resolve(impact=None, config=config)
        multiplier = _TIER_BUDGET_MULTIPLIERS.get(tier, _MODERATE_BUDGET_MULTIPLIER)
        return max(1, round(base_tokens * multiplier))

    return _scale


def _render_premortem(output: PremortemOutput) -> str:
    """Render premortem output as a markdown section.

    Returns:
        A markdown block listing failure modes and assumptions, or an
        empty string when the analysis surfaced neither.
    """
    if not output.failure_modes and not output.assumptions:
        return ""
    lines: list[str] = []
    if output.failure_modes:
        lines.append("### Failure modes")
        lines.extend(
            f"- ({fm.likelihood}/{fm.impact}) {fm.description}"
            f" [mitigation: {fm.mitigation}]"
            for fm in output.failure_modes
        )
    if output.assumptions:
        lines.append("### Key assumptions")
        lines.extend(f"- {assumption}" for assumption in output.assumptions)
    return "\n".join(lines)
