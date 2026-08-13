# module-kind: code
"""Best-effort capability classification for stakes routing.

Maps a configured model onto one of the rungs
(:data:`~synthorg.core.types.CapabilityLevel`: ``basic`` < ``capable`` <
``expert``) from its real capability metadata, cheapest signal first. The
result is a *best-effort* classification carrying a confidence and a
human-readable reason so the assignment is explicit and operator-visible,
never a silent guess. It always yields a rung (never ``None``): stakes
routing must be able to reason about every configured model, escalating when
none clears the floor rather than falling back to an unclassifiable state.

Every signal here is a **proxy** -- size, price, a vendor's own usage band --
and a proxy is what let an older, larger, dearer model outrank a newer one
that benchmarked above it. Evidence that measures capability directly
overrides this layer rather than tuning it.

This is the routing counterpart to :mod:`synthorg.budget.model_capability`,
which resolves the vendor-agnostic ``example-<rung>`` archetype ids for the
Pareto cost/quality view. Here the input is a live, operator-configured model.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.model_capability import heuristic_capability
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.provider_schema import ProviderModelConfig
from synthorg.core.types import CapabilityLevel, NotBlankStr

# ``cost_tier`` (1-4, light -> extra heavy) is the most direct strength signal:
# for ollama it is the real scraped per-model usage level; elsewhere it is
# derived from cost/size at discovery. Collapse the 4-band scale onto the three
# rungs (economical -> basic, balanced -> capable, high/quality -> expert),
# matching the capability-demand split in ``templates.model_matcher_tiering``.
_COST_TIER_TO_CAPABILITY: Final[Mapping[int, CapabilityLevel]] = MappingProxyType(
    {1: "basic", 2: "capable", 3: "expert", 4: "expert"},
)

# Parameter-count bands (absolute parameter counts). Below the lower ceiling a
# model is a light worker; at or above the upper floor it is a heavyweight;
# between them it is a mid model. Bands mirror the matcher's economical/high
# intuition (a ~30B mid model is capable, a >=70B frontier model an expert).
_PARAM_BASIC_CEILING: Final[int] = 15_000_000_000
_PARAM_EXPERT_FLOOR: Final[int] = 70_000_000_000

# Cost-per-1k proxy bands (base currency) used only for a PAID model when
# neither ``cost_tier`` nor ``parameter_count`` is known. A local/free model
# (total cost 0) is never demoted here: its rung is decided by a capability
# signal or the neutral default, so a free local model is not forced to
# ``basic`` purely for being free.
_COST_BASIC_CEILING: Final[float] = 0.001
_COST_EXPERT_FLOOR: Final[float] = 0.01

# Confidence per signal. ``cost_tier`` is a real scraped/derived band (high);
# ``parameter_count`` and cost are coarser proxies (medium); the neutral default
# is an explicit best-effort guess (low) the operator is expected to review.
_CONF_COST_TIER: Final[float] = 0.9
_CONF_PARAM: Final[float] = 0.7
_CONF_COST: Final[float] = 0.6
_CONF_DEFAULT: Final[float] = 0.3

# Neutral best-effort rung when no signal is known. ``capable`` keeps
# normal-stakes work routable while a high-stakes task still escalates: an
# unknown model must not silently satisfy an expert floor.
_DEFAULT_CAPABILITY: Final[CapabilityLevel] = "capable"


class CapabilityClassification(BaseModel):
    """A best-effort capability classification for one model.

    Attributes:
        capability: The rung the model is classified into.
        confidence: How trustworthy the classification is (0-1); driven by
            which signal decided it.
        reason: Human-readable explanation naming the deciding signal.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    capability: CapabilityLevel = Field(description="Capability rung")
    confidence: float = Field(ge=0.0, le=1.0, description="Classification confidence")
    reason: NotBlankStr = Field(description="Deciding signal, human-readable")


def classify_model_capability(
    metadata: ModelMetadata,
    *,
    model_id: str,
    total_cost_per_1k: float,
) -> CapabilityClassification:
    """Classify a model into a rung from its id, metadata, and cost.

    Signals are tried strongest-first: an ``example-<rung>`` archetype id (the
    canonical vendor-agnostic vocabulary), then an authoritative
    scraped/derived ``cost_tier``, then ``parameter_count`` size bands, then
    per-1k cost as a proxy for a paid model, then a neutral best-effort default.

    Returns:
        The :class:`CapabilityClassification` for the model. Always populated;
        an unknown model resolves to the neutral default with low confidence.
    """
    archetype = heuristic_capability(model_id)
    if archetype is not None:
        return CapabilityClassification(
            capability=archetype,
            confidence=_CONF_COST_TIER,
            reason=f"archetype id capability={archetype}",
        )
    if metadata.cost_tier is not None:
        return CapabilityClassification(
            capability=_COST_TIER_TO_CAPABILITY[metadata.cost_tier],
            confidence=_CONF_COST_TIER,
            reason=f"scraped/derived cost_tier={metadata.cost_tier}",
        )
    if metadata.parameter_count is not None:
        if metadata.parameter_count < _PARAM_BASIC_CEILING:
            capability: CapabilityLevel = "basic"
        elif metadata.parameter_count >= _PARAM_EXPERT_FLOOR:
            capability = "expert"
        else:
            capability = "capable"
        return CapabilityClassification(
            capability=capability,
            confidence=_CONF_PARAM,
            reason=f"parameter_count={metadata.parameter_count}",
        )
    if total_cost_per_1k > 0.0:
        if total_cost_per_1k < _COST_BASIC_CEILING:
            capability = "basic"
        elif total_cost_per_1k >= _COST_EXPERT_FLOOR:
            capability = "expert"
        else:
            capability = "capable"
        return CapabilityClassification(
            capability=capability,
            confidence=_CONF_COST,
            reason=f"cost_per_1k={total_cost_per_1k:g}",
        )
    return CapabilityClassification(
        capability=_DEFAULT_CAPABILITY,
        confidence=_CONF_DEFAULT,
        reason="no capability or cost signal; neutral best-effort default",
    )


@runtime_checkable
class ModelCapabilityClassifier(Protocol):
    """Classifies a configured model into a routing tier (best-effort)."""

    def classify(self, model_config: ProviderModelConfig) -> CapabilityClassification:
        """Return the tier classification for *model_config*."""
        ...


class HeuristicTierClassifier:
    """Deterministic :class:`ModelCapabilityClassifier` over capability metadata."""

    def classify(self, model_config: ProviderModelConfig) -> CapabilityClassification:
        """Classify *model_config* from its metadata and total per-1k cost.

        Returns:
            The heuristic :class:`CapabilityClassification`.
        """
        total = model_config.cost_per_1k_input + model_config.cost_per_1k_output
        return classify_model_capability(
            model_config.metadata,
            model_id=model_config.id,
            total_cost_per_1k=total,
        )


__all__ = [
    "CapabilityClassification",
    "HeuristicTierClassifier",
    "ModelCapabilityClassifier",
    "classify_model_capability",
]
