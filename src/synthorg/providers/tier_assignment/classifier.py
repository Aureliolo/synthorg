# module-kind: code
"""Best-effort model-tier classification for stakes routing.

Maps a configured model onto one of the routing tiers
(:data:`~synthorg.core.types.ModelTier`: ``small`` < ``medium`` < ``large``)
from its real capability metadata, cheapest signal first. The result is a
*best-effort* classification carrying a confidence and a human-readable reason
so the assignment is explicit and operator-visible, never a silent guess. It
always yields a tier (never ``None``): stakes routing must be able to reason
about every configured model, escalating when none clears the required tier
rather than falling back to an unclassifiable state.

This is the routing counterpart to :mod:`synthorg.budget.model_tier`, which
resolves the vendor-agnostic ``example-<tier>`` archetype ids for the Pareto
cost/quality view. Here the input is a live, operator-configured model.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.model_tier import heuristic_tier
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.core.types import ModelTier, NotBlankStr

# ``cost_tier`` (1-4, light -> extra heavy) is the most direct strength signal:
# for ollama it is the real scraped per-model usage level; elsewhere it is
# derived from cost/size at discovery. Collapse the 4-band scale onto the three
# routing tiers (economical -> small, balanced -> medium, high/quality -> large),
# matching the capability-demand split in ``templates.model_matcher_tiering``.
_COST_TIER_TO_ROUTING: Final[Mapping[int, ModelTier]] = MappingProxyType(
    {1: "small", 2: "medium", 3: "large", 4: "large"},
)

# Parameter-count strength bands (absolute parameter counts). Below the small
# ceiling a model is a light worker; at or above the large floor it is a
# heavyweight; between them it is a mid model. Bands mirror the matcher's
# economical/high intuition (a ~30B mid model is medium, a >=70B frontier model
# is large).
_PARAM_SMALL_CEILING: Final[int] = 15_000_000_000
_PARAM_LARGE_FLOOR: Final[int] = 70_000_000_000

# Cost-per-1k proxy bands (base currency) used only for a PAID model when
# neither ``cost_tier`` nor ``parameter_count`` is known. A local/free model
# (total cost 0) is never demoted here: its tier is decided by a capability
# signal or the neutral default, so a free local model is not forced to small
# purely for being free.
_COST_SMALL_CEILING: Final[float] = 0.001
_COST_LARGE_FLOOR: Final[float] = 0.01

# Confidence per signal. ``cost_tier`` is a real scraped/derived tier (high);
# ``parameter_count`` and cost are coarser proxies (medium); the neutral default
# is an explicit best-effort guess (low) the operator is expected to review.
_CONF_COST_TIER: Final[float] = 0.9
_CONF_PARAM: Final[float] = 0.7
_CONF_COST: Final[float] = 0.6
_CONF_DEFAULT: Final[float] = 0.3

# Neutral best-effort tier when no signal is known. ``medium`` keeps
# normal-stakes work routable while a high-stakes task still escalates: an
# unknown model must not silently satisfy the large-tier requirement.
_DEFAULT_TIER: Final[ModelTier] = "medium"

# The archetype id maps to a 4-value tier that includes ``local-small``; the
# routing vocabulary has three tiers, so a local-small archetype routes as
# ``small``.
_ARCHETYPE_TO_ROUTING: Final[Mapping[str, ModelTier]] = MappingProxyType(
    {"large": "large", "medium": "medium", "small": "small", "local-small": "small"},
)


class TierClassification(BaseModel):
    """A best-effort tier classification for one model.

    Attributes:
        tier: The routing tier the model is classified into.
        confidence: How trustworthy the classification is (0-1); driven by
            which signal decided it.
        reason: Human-readable explanation naming the deciding signal.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tier: ModelTier = Field(description="Routing tier")
    confidence: float = Field(ge=0.0, le=1.0, description="Classification confidence")
    reason: NotBlankStr = Field(description="Deciding signal, human-readable")


def classify_model_tier(
    metadata: ModelMetadata,
    *,
    model_id: str,
    total_cost_per_1k: float,
) -> TierClassification:
    """Classify a model into a routing tier from its id, metadata, and cost.

    Signals are tried strongest-first: an ``example-<tier>`` archetype id (the
    canonical vendor-agnostic tier vocabulary), then an authoritative
    scraped/derived ``cost_tier``, then ``parameter_count`` size bands, then
    per-1k cost as a proxy for a paid model, then a neutral best-effort default.

    Returns:
        The :class:`TierClassification` for the model. Always populated; an
        unknown model resolves to the neutral default tier with low confidence.
    """
    archetype = heuristic_tier(model_id)
    if archetype is not None:
        return TierClassification(
            tier=_ARCHETYPE_TO_ROUTING[archetype],
            confidence=_CONF_COST_TIER,
            reason=f"archetype id tier={archetype}",
        )
    if metadata.cost_tier is not None:
        return TierClassification(
            tier=_COST_TIER_TO_ROUTING[metadata.cost_tier],
            confidence=_CONF_COST_TIER,
            reason=f"scraped/derived cost_tier={metadata.cost_tier}",
        )
    if metadata.parameter_count is not None:
        if metadata.parameter_count < _PARAM_SMALL_CEILING:
            tier: ModelTier = "small"
        elif metadata.parameter_count >= _PARAM_LARGE_FLOOR:
            tier = "large"
        else:
            tier = "medium"
        return TierClassification(
            tier=tier,
            confidence=_CONF_PARAM,
            reason=f"parameter_count={metadata.parameter_count}",
        )
    if total_cost_per_1k > 0.0:
        if total_cost_per_1k < _COST_SMALL_CEILING:
            tier = "small"
        elif total_cost_per_1k >= _COST_LARGE_FLOOR:
            tier = "large"
        else:
            tier = "medium"
        return TierClassification(
            tier=tier,
            confidence=_CONF_COST,
            reason=f"cost_per_1k={total_cost_per_1k:g}",
        )
    return TierClassification(
        tier=_DEFAULT_TIER,
        confidence=_CONF_DEFAULT,
        reason="no capability or cost signal; neutral best-effort default",
    )


@runtime_checkable
class ModelTierClassifier(Protocol):
    """Classifies a configured model into a routing tier (best-effort)."""

    def classify(self, model_config: ProviderModelConfig) -> TierClassification:
        """Return the tier classification for *model_config*."""
        ...


class HeuristicTierClassifier:
    """Deterministic :class:`ModelTierClassifier` over capability metadata."""

    def classify(self, model_config: ProviderModelConfig) -> TierClassification:
        """Classify *model_config* from its metadata and total per-1k cost.

        Returns:
            The heuristic :class:`TierClassification`.
        """
        total = model_config.cost_per_1k_input + model_config.cost_per_1k_output
        return classify_model_tier(
            model_config.metadata,
            model_id=model_config.id,
            total_cost_per_1k=total,
        )


__all__ = [
    "HeuristicTierClassifier",
    "ModelTierClassifier",
    "TierClassification",
    "classify_model_tier",
]
