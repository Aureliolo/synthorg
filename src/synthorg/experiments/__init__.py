"""A/B test variant registry.

Operators register experiment variants (the alternatives under test)
and ask the service to assign a subject (agent, user, project) to one
variant deterministically. The assignment is hashed by ``(experiment,
subject_id)`` so the same subject always lands on the same variant;
the persistence layer records every assignment for audit and rollout
analysis.

The service is intentionally thin: it owns variant CRUD plus the
assignment computation. Higher-level orchestration (rollout
percentages, ramp-up curves, kill-switches) layers on top via the
existing settings registry.
"""

from synthorg.experiments.models import (
    ExperimentAssignment,
    ExperimentVariant,
)
from synthorg.experiments.protocol import ExperimentRepository
from synthorg.experiments.service import ExperimentService

__all__ = (
    "ExperimentAssignment",
    "ExperimentRepository",
    "ExperimentService",
    "ExperimentVariant",
)
