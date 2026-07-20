"""Project lifecycle event constants.

The initiative-level lifecycle: a project advances from its own work as its
plan's items roll up. Distinct from ``events.api`` (which records operator CRUD
on a project) and ``events.project_brain`` (per-project decision memory).
"""

from typing import Final

PROJECT_TRANSITION: Final[str] = "project.transition"
PROJECT_TRANSITION_INVALID: Final[str] = "project.transition.invalid"
PROJECT_TRANSITION_CONFIG_ERROR: Final[str] = "project.transition.config_error"
PROJECT_PLAN_LINKED: Final[str] = "project.plan.linked"
PROJECT_ROLLUP_STARTED: Final[str] = "project.rollup.started"
PROJECT_ROLLUP_COMPLETED: Final[str] = "project.rollup.completed"
PROJECT_ROLLUP_SKIPPED: Final[str] = "project.rollup.skipped"
PROJECT_ROLLUP_FAILED: Final[str] = "project.rollup.failed"
PROJECT_ROLLUP_CONFLICT_RETRY: Final[str] = "project.rollup.conflict_retry"
PROJECT_ROLLUP_CONFLICT_EXHAUSTED: Final[str] = "project.rollup.conflict_exhausted"
