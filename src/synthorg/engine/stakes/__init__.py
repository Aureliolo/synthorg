"""Per-subtask stakes assessment for stakes-aware model routing.

A :class:`StakesAssessor` classifies how consequential a subtask or task
is (:class:`~synthorg.core.enums.Stakes`). The routing layer
(:mod:`synthorg.engine.routing_policy`) consumes the result to pick a
cheap model for low-stakes work and a strong model plus a red-team
review for high-stakes work.
"""

from synthorg.engine.stakes.config import (
    DEFAULT_COMPLEXITY_STAKES_RULES,
    ComplexityStakesRule,
    StakesAssessmentConfig,
)
from synthorg.engine.stakes.factory import build_stakes_assessor
from synthorg.engine.stakes.heuristic import DefaultStakesAssessor
from synthorg.engine.stakes.protocol import StakesAssessor

__all__ = [
    "DEFAULT_COMPLEXITY_STAKES_RULES",
    "ComplexityStakesRule",
    "DefaultStakesAssessor",
    "StakesAssessmentConfig",
    "StakesAssessor",
    "build_stakes_assessor",
]
