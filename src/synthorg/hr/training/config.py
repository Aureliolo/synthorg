"""Training mode configuration.

Frozen Pydantic configuration model with safe defaults for all
training pipeline components.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.hr.training.models import ContentType

# Type alias for serialized strategy config values.
_ConfigValue = int | float | str | bool


def _default_selector_config() -> dict[str, _ConfigValue]:
    return {"top_n": 3}


def _default_curation_config() -> dict[str, _ConfigValue]:
    return {"top_k": 50}


class TrainingConfig(BaseModel):
    """Configuration for the training pipeline.

    All fields have safe defaults: training enabled, all extractors
    active, relevance-based curation, all guards on, human review
    required.

    Attributes:
        enabled: Whether training mode is active.
        source_selector_type: Source selector strategy name.
        source_selector_config: Serialized config for the selector.
        curation_strategy_type: Curation strategy name.
        curation_strategy_config: Serialized config for curation.
        default_volume_caps: Default per-content-type hard limits.
        require_review_by_default: Whether human review is required.
        sanitization_max_length: Max content length after sanitization.
        training_namespace: Memory namespace for stored items.
        training_tags: Default tags applied to stored items.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Whether training mode is active",
    )
    source_selector_type: NotBlankStr = Field(
        default="role_top_performers",
        description="Source selector strategy name",
    )
    source_selector_config: dict[str, _ConfigValue] = Field(
        default_factory=_default_selector_config,
        description="Serialized config for the selector",
    )
    curation_strategy_type: NotBlankStr = Field(
        default="relevance",
        description="Curation strategy name",
    )
    curation_strategy_config: dict[str, _ConfigValue] = Field(
        default_factory=_default_curation_config,
        description="Serialized config for curation",
    )
    default_volume_caps: dict[ContentType, int] = Field(
        default_factory=lambda: {
            ContentType.PROCEDURAL: 50,
            ContentType.SEMANTIC: 10,
            ContentType.TOOL_PATTERNS: 20,
        },
        description="Default per-content-type hard limits",
    )
    require_review_by_default: bool = Field(
        default=True,
        description="Whether human review is required by default",
    )
    sanitization_max_length: int = Field(
        default=2000,
        gt=0,
        description="Max content length after sanitization",
    )
    training_namespace: NotBlankStr = Field(
        default="training",
        description="Memory namespace for stored items",
    )
    training_tags: tuple[NotBlankStr, ...] = Field(
        default=("learned_from_seniors",),
        description="Default tags applied to stored items",
    )
