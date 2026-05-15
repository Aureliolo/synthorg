"""Event-name constants for the A/B experiment registry."""

from typing import Final

EXPERIMENT_VARIANT_REGISTERED: Final[str] = "experiments.variant.registered"
EXPERIMENT_VARIANT_DELETED: Final[str] = "experiments.variant.deleted"
EXPERIMENT_ASSIGNMENT_COMPUTED: Final[str] = "experiments.assignment.computed"
EXPERIMENT_ASSIGNMENT_REPLAYED: Final[str] = "experiments.assignment.replayed"
