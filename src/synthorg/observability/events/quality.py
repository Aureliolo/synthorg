"""Step-level quality signal event constants."""

from typing import Final

QUALITY_STEP_CLASSIFIED: Final[str] = "execution.quality.step_classified"
QUALITY_STEP_CLASSIFICATION_FAILED: Final[str] = (
    "execution.quality.step_classification_failed"
)
QUALITY_CLASSIFIER_CONFIG_INVALID: Final[str] = (
    "execution.quality.classifier_config_invalid"
)
QUALITY_ACCURACY_EFFORT_COMPUTED: Final[str] = (
    "execution.quality.accuracy_effort_computed"
)
QUALITY_WEAK_MODEL_WARNING: Final[str] = "execution.quality.weak_model_warning"

# -- MCP audit events ----------------------------------------------------

REVIEW_CREATED_VIA_MCP: Final[str] = "quality.review.created_via_mcp"
REVIEW_UPDATED_VIA_MCP: Final[str] = "quality.review.updated_via_mcp"

QUALITY_CAPABILITY_UNAVAILABLE: Final[str] = "quality.capability_unavailable"
