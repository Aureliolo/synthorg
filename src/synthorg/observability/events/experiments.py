"""Event-name constants for the A/B experiment registry."""

from typing import Final

EXPERIMENT_VARIANT_REGISTERED: Final[str] = "experiments.variant.registered"
EXPERIMENT_VARIANT_DELETED: Final[str] = "experiments.variant.deleted"
EXPERIMENT_VARIANT_INVALID_WEIGHT: Final[str] = "experiments.variant.invalid_weight"
EXPERIMENT_ASSIGNMENT_COMPUTED: Final[str] = "experiments.assignment.computed"
EXPERIMENT_ASSIGNMENT_REPLAYED: Final[str] = "experiments.assignment.replayed"
# Emitted before the ``assign()`` raise so operators see which
# experiment was queried with no variants registered, rather than
# only the controller's error envelope.
EXPERIMENT_NOT_FOUND: Final[str] = "experiments.not_found"
