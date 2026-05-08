"""Workflow blueprint event name constants for observability."""

from typing import Final

BLUEPRINT_LIST: Final[str] = "workflow.blueprint.list"
"""Blueprint listing operation."""

BLUEPRINT_LOAD_START: Final[str] = "workflow.blueprint.load.start"
"""Blueprint load started."""

BLUEPRINT_LOAD_SUCCESS: Final[str] = "workflow.blueprint.load.success"
"""Blueprint loaded successfully."""

BLUEPRINT_LOAD_NOT_FOUND: Final[str] = "workflow.blueprint.load.not_found"
"""Blueprint not found or name validation failed."""

BLUEPRINT_INSTANTIATE_START: Final[str] = "workflow.blueprint.instantiate.start"
"""Workflow creation from blueprint started."""

BLUEPRINT_INSTANTIATE_SUCCESS: Final[str] = "workflow.blueprint.instantiate.success"
"""Workflow created from blueprint successfully."""

BLUEPRINT_INSTANTIATE_FAILED: Final[str] = "workflow.blueprint.instantiate.failed"
"""Workflow creation from blueprint failed."""

BLUEPRINT_INSTANTIATE_OUTCOME: Final[str] = "workflow.blueprint.instantiate.outcome"
"""Terminal blueprint instantiation event used as the metric chokepoint.

Emitted exactly once per instantiation attempt regardless of outcome
(success / validation_error / not_found / unknown_error) so the
matching Counter never double-counts. Carries ``blueprint_name``,
``outcome``, and ``duration_sec``."""
