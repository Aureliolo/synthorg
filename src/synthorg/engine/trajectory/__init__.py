"""Trajectory scoring for best-of-K candidate selection.

Provides self-consistency filtering, verbalized confidence scoring,
trace length scoring, and budget-guarded K-candidate sampling.
"""

from synthorg.engine.trajectory.budget_guard import (
    check_trajectory_budget,
)
from synthorg.engine.trajectory.models import (
    CandidateResult,
    TrajectoryConfig,
    TrajectoryScore,
)
from synthorg.engine.trajectory.scorer import TrajectoryScorer

__all__ = [
    "CandidateResult",
    "TrajectoryConfig",
    "TrajectoryScore",
    "TrajectoryScorer",
    "check_trajectory_budget",
]
