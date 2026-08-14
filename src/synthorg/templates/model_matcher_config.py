# module-kind: code
"""Operator-tunable weights and capability derivation for the model matcher.

Separated from the matching engine (:mod:`synthorg.templates.model_matcher`)
so the engine stays under its size budget. This module owns only the
config model, its settings-projected default, and the report-only capability
derivation; the engine re-exports these names.
"""

from collections.abc import Mapping
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.types import CapabilityLevel
from synthorg.settings.bridge_configs import EngineBridgeConfig
from synthorg.templates.model_matcher_tiering import DEFAULT_TIER_OVERRIDES

# Smallest parameter count a model may have to be auto-assigned to an agent.
# Sub-this models (e.g. a 1B) cannot reliably run an agent loop (tool calls,
# multi-step reasoning), so the demand path excludes them; they stay manually
# selectable and an explicit family/pattern/id reference still honours them.
_MIN_USABLE_PARAMETERS: Final[int] = 14_000_000_000


def _default_tier_overrides() -> dict[str, int]:
    """Return a fresh mutable copy of the curated tier overrides.

    Returns:
        A new dict so the frozen-config default is neither shared across
        instances nor an un-deep-copyable ``mappingproxy``.
    """
    return dict(DEFAULT_TIER_OVERRIDES)


class ModelMatcherConfig(BaseModel):
    """Operator-tunable weights for the capability-aware matcher.

    The score of a surviving candidate is ``base_score`` plus the
    capability-fit, context-headroom, and priority bonuses, capped at
    1.0.  ``expert_min_context`` / ``capable_min_context`` derive the
    report-only rung.

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
    expert_min_context: int = Field(default=200_000, gt=0)
    capable_min_context: int = Field(default=32_000, gt=0)
    min_usable_parameters: int = Field(default=_MIN_USABLE_PARAMETERS, ge=0)
    prefer_local: bool = Field(default=True)
    min_cloud_tier: int = Field(default=2, ge=1, le=4)
    tier_overrides: Mapping[str, int] = Field(
        default_factory=_default_tier_overrides,
    )

    @model_validator(mode="after")
    def _validate_tier_thresholds(self) -> Self:
        """Ensure the expert-rung threshold sits above the capable-rung one.

        Returns:
            The validated instance (``self``), unchanged.

        Raises:
            ValueError: When ``expert_min_context`` is not strictly
                greater than ``capable_min_context`` (which would make
                the capable band unreachable).
        """
        if self.expert_min_context <= self.capable_min_context:
            msg = (
                "expert_min_context must exceed capable_min_context; "
                f"got {self.expert_min_context} <= {self.capable_min_context}"
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
            expert_min_context=bridge.matcher_expert_min_context,
            capable_min_context=bridge.matcher_capable_min_context,
            min_usable_parameters=bridge.matcher_min_usable_parameters,
            prefer_local=bridge.matcher_prefer_local,
            min_cloud_tier=bridge.matcher_min_cloud_cost_tier,
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


def derive_capability(
    model: ProviderModelConfig,
    config: ModelMatcherConfig,
) -> CapabilityLevel:
    """Derive the report-only rung from a model's context window.

    Context length is a proxy, not a measurement: a long window says a model
    can read a lot, never that it reasons well over it. Selection never
    depends on this label.

    Returns:
        ``"expert"`` / ``"capable"`` / ``"basic"`` by absolute context
        thresholds (operator-tunable).
    """
    if model.max_context >= config.expert_min_context:
        return "expert"
    if model.max_context >= config.capable_min_context:
        return "capable"
    return "basic"
