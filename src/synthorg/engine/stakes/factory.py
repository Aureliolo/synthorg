"""Factory for building a stakes assessor from config.

Dispatches on ``StakesAssessmentConfig.assessor`` via a
``StrategyRegistry`` (mirrors ``loop_selector._LOOP_REGISTRY``). Ships
the deterministic heuristic as the only built-in; additional assessors
(e.g. an LLM-backed one) register here without touching call sites.
"""

from synthorg.core.registry import StrategyRegistry
from synthorg.engine.stakes.config import StakesAssessmentConfig
from synthorg.engine.stakes.heuristic import DefaultStakesAssessor
from synthorg.engine.stakes.protocol import (
    StakesAssessor,
)


def _build_heuristic(config: StakesAssessmentConfig) -> StakesAssessor:
    return DefaultStakesAssessor(config)


_ASSESSOR_REGISTRY: StrategyRegistry[StakesAssessor] = StrategyRegistry(
    {"heuristic": _build_heuristic},
    kind="stakes_assessor",
)


def build_stakes_assessor(
    config: StakesAssessmentConfig | None = None,
) -> StakesAssessor:
    """Build a :class:`StakesAssessor` from *config*.

    Args:
        config: Assessment config; defaults to the built-in heuristic
            rubric.

    Returns:
        A concrete stakes assessor.

    Raises:
        StrategyFactoryNotFoundError: If ``config.assessor`` is unknown.
    """
    resolved = config or StakesAssessmentConfig()
    return _ASSESSOR_REGISTRY.build(resolved.assessor, resolved)
