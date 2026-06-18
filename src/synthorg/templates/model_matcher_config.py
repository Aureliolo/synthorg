"""Operator-tunable weights and tier derivation for the model matcher.

Separated from the matching engine (:mod:`synthorg.templates.model_matcher`)
so the engine stays under its size budget. This module owns only the
config model, its settings-projected default, and the report-only tier
derivation; the engine re-exports these names.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.types import ModelTier
from synthorg.settings.bridge_configs import EngineBridgeConfig


class ModelMatcherConfig(BaseModel):
    """Operator-tunable weights for the capability-aware matcher.

    The score of a surviving candidate is ``base_score`` plus the
    capability-fit, context-headroom, and priority bonuses, capped at
    1.0.  ``tier_*_min_context`` derive the report-only tier label.

    Field defaults mirror the registered defaults in
    :mod:`synthorg.settings.definitions.engine`. Runtime callers passing
    ``matcher_config=None`` fall back to ``DEFAULT_MATCHER_CONFIG``,
    projected from a default ``EngineBridgeConfig`` so the canonical
    settings registration is the single source of truth.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    base_score: float = Field(default=0.4, ge=0.0, le=1.0)
    capability_fit_weight: float = Field(default=0.2, ge=0.0, le=1.0)
    headroom_max_bonus: float = Field(default=0.2, ge=0.0, le=1.0)
    priority_max_bonus: float = Field(default=0.2, ge=0.0, le=1.0)
    headroom_ratio_cap: float = Field(default=2.0, ge=1.0, le=100.0)
    tier_large_min_context: int = Field(default=200_000, gt=0)
    tier_medium_min_context: int = Field(default=32_000, gt=0)

    @model_validator(mode="after")
    def _validate_tier_thresholds(self) -> Self:
        """Ensure the large tier threshold sits above the medium one.

        Returns:
            The validated instance (``self``), unchanged.

        Raises:
            ValueError: When ``tier_large_min_context`` is not strictly
                greater than ``tier_medium_min_context`` (which would make
                the medium band unreachable).
        """
        if self.tier_large_min_context <= self.tier_medium_min_context:
            msg = (
                "tier_large_min_context must exceed tier_medium_min_context; "
                f"got {self.tier_large_min_context} <= {self.tier_medium_min_context}"
            )
            raise ValueError(msg)
        return self

    @classmethod
    def from_bridge_config(cls, bridge: EngineBridgeConfig) -> ModelMatcherConfig:
        """Project the matcher subset out of an ``EngineBridgeConfig``.

        Returns:
            A ``ModelMatcherConfig`` carrying the matcher-relevant fields
            projected from ``bridge``.
        """
        return cls(
            base_score=bridge.matcher_base_score,
            capability_fit_weight=bridge.matcher_capability_fit_weight,
            headroom_max_bonus=bridge.matcher_headroom_max_bonus,
            priority_max_bonus=bridge.matcher_priority_max_bonus,
            headroom_ratio_cap=bridge.matcher_headroom_ratio_cap,
            tier_large_min_context=bridge.matcher_tier_large_min_context,
            tier_medium_min_context=bridge.matcher_tier_medium_min_context,
        )


def _build_default_matcher_config() -> ModelMatcherConfig:
    """Project the matcher defaults out of a default ``EngineBridgeConfig``.

    Returns:
        A ``ModelMatcherConfig`` projected from a default
        ``EngineBridgeConfig`` so the no-config path tracks the
        registered defaults rather than this module's field defaults.
    """
    return ModelMatcherConfig.from_bridge_config(EngineBridgeConfig())


DEFAULT_MATCHER_CONFIG = _build_default_matcher_config()


def derive_tier(model: ProviderModelConfig, config: ModelMatcherConfig) -> ModelTier:
    """Derive the report-only tier label from a model's context window.

    Returns:
        ``"large"`` / ``"medium"`` / ``"small"`` by absolute context
        thresholds (operator-tunable). Selection never depends on this.
    """
    if model.max_context >= config.tier_large_min_context:
        return "large"
    if model.max_context >= config.tier_medium_min_context:
        return "medium"
    return "small"
