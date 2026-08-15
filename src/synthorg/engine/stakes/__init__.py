"""Per-subtask stakes assessment for capability-based agent selection.

A :class:`StakesAssessor` classifies how consequential a subtask or task
is (:class:`~synthorg.core.task_enums.Stakes`). The capability policy
(:mod:`synthorg.engine.routing_policy`) consumes the result to decide which
rung an agent must run at to take the work, and whether the deliverable
needs a red-team review.
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
