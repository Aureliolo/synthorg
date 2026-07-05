# module-kind: code
"""In-family model-upgrade recommender.

The inverse of the budget downgrade recommender
(:class:`~synthorg.budget.optimizer.CostOptimizer`): it groups a
provider's configured models by ``metadata.family`` and, within each
family, surfaces every model older than the newest generation as an
upgrade candidate.  Scoring reuses the registered matcher weights
(:class:`~synthorg.templates.model_matcher_config.ModelMatcherConfig`,
projected from ``settings/definitions/engine.py``) so there is no
parallel knob.  It is pin-and-recommend: it never mutates config; a
human (or the opt-in auto-apply flow) acts on the recommendation.
"""

from collections.abc import Mapping

from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.schema import ProviderConfig, ProviderModelConfig
from synthorg.core.types import flatten_label
from synthorg.providers.management.upgrade_models import (
    UpgradeAnalysis,
    UpgradeRecommendation,
)
from synthorg.templates.model_matcher_config import (
    DEFAULT_MATCHER_CONFIG,
    ModelMatcherConfig,
)

_GENERATION_HALF_SATURATION: float = 1.0
"""Generation gap at which the generation score component reaches 0.5.

``gap / (gap + k)`` maps an unbounded positive generation delta into
``(0, 1)``; ``k = 1.0`` means a one-generation jump scores 0.5 on that
axis. A named constant rather than a bare literal in the scorer.
"""


def _capabilities(meta: ModelMetadata) -> tuple[bool, bool, bool]:
    """Return the (tools, vision, reasoning) capability flags."""
    return (meta.supports_tools, meta.supports_vision, meta.supports_reasoning)


def _has_capability_regression(
    current: ModelMetadata, candidate: ModelMetadata
) -> bool:
    """Return True if *candidate* drops a capability *current* has.

    Returns:
        ``True`` when any capability supported by the current model is
        not supported by the candidate (so it is not a safe upgrade).
    """
    pairs = zip(_capabilities(current), _capabilities(candidate), strict=True)
    return any(cur and not cand for cur, cand in pairs)


def _score(
    *,
    current: ProviderModelConfig,
    candidate: ProviderModelConfig,
    current_generation: float,
    candidate_generation: float,
    config: ModelMatcherConfig,
) -> float:
    """Score an upgrade in [0, 1] from the matcher weights.

    Returns:
        ``base_score`` plus capability-fit, context-headroom, and
        generation-delta bonuses, capped at 1.0.
    """
    caps = _capabilities(candidate.metadata)
    capability_fraction = sum(caps) / len(caps)

    ratio = (
        candidate.max_context / current.max_context if current.max_context > 0 else 1.0
    )
    clamped = min(max(ratio, 1.0), config.headroom_ratio_cap)
    span = config.headroom_ratio_cap - 1.0
    headroom_factor = (clamped - 1.0) / span if span > 0 else 0.0

    gap = candidate_generation - current_generation
    generation_factor = gap / (gap + _GENERATION_HALF_SATURATION)

    raw = (
        config.base_score
        + config.capability_fit_weight * capability_fraction
        + config.headroom_max_bonus * headroom_factor
        + config.priority_max_bonus * generation_factor
    )
    return min(raw, 1.0)


class UpgradeRecommender:
    """Recommends newer in-family models for configured providers."""

    def __init__(self, *, matcher_config: ModelMatcherConfig | None = None) -> None:
        """Initialise the recommender.

        Args:
            matcher_config: Operator-tunable matcher weights; defaults to
                the settings-projected ``DEFAULT_MATCHER_CONFIG`` so the
                registered defaults remain the single source of truth.
        """
        self._config = matcher_config or DEFAULT_MATCHER_CONFIG

    def recommend(
        self,
        providers: Mapping[str, ProviderConfig],
    ) -> UpgradeAnalysis:
        """Find newer in-family models across all configured providers.

        Returns:
            An :class:`UpgradeAnalysis`; empty when no family has a
            newer-generation model without a capability regression.
        """
        recommendations: list[UpgradeRecommendation] = []
        for provider_name, provider in providers.items():
            recommendations.extend(
                self._recommend_for_provider(provider_name, provider)
            )
        return UpgradeAnalysis(recommendations=tuple(recommendations))

    def _best_candidate(
        self,
        current: ProviderModelConfig,
        current_generation: float,
        newest_candidates: list[ProviderModelConfig],
        newest_gen: float,
    ) -> tuple[ProviderModelConfig, float] | None:
        """Pick the strongest newest-gen candidate without a regression.

        Ranks the regression-free candidates by upgrade score (which
        rewards capability fit and context headroom) so a larger / more
        capable sibling at the same generation (e.g. a ``-pro`` variant) is
        preferred over a smaller / cheaper one (e.g. a ``-flash`` variant)
        rather than an arbitrary alphabetical pick. Id breaks a score tie
        deterministically.

        Returns:
            The highest-scoring viable candidate paired with its score, or
            ``None`` when every newest-gen candidate would drop a capability
            the current has.
        """
        scored = [
            (
                c,
                _score(
                    current=current,
                    candidate=c,
                    current_generation=current_generation,
                    candidate_generation=newest_gen,
                    config=self._config,
                ),
            )
            for c in newest_candidates
            if not _has_capability_regression(current.metadata, c.metadata)
        ]
        if not scored:
            return None
        return max(scored, key=lambda pair: (pair[1], pair[0].id))

    def _recommend_for_provider(
        self,
        provider_name: str,
        provider: ProviderConfig,
    ) -> list[UpgradeRecommendation]:
        """Build upgrade recommendations for one provider's families.

        Returns:
            The list of recommendations (possibly empty).
        """
        # Group by (family, embedding-class): a family label alone spans two
        # incompatible model classes -- an embedding model (vector output)
        # and a chat model can share one family label yet are not drop-in
        # replacements for each other, so a newer-generation chat model must
        # never surface as the upgrade for an embedding model (or vice versa).
        by_group: dict[tuple[str, bool], list[tuple[ProviderModelConfig, float]]] = {}
        for model in provider.models:
            family = model.metadata.family
            generation = model.metadata.generation
            if family is None or generation is None or model.stale is not None:
                continue
            key = (family, model.metadata.supports_embeddings)
            by_group.setdefault(key, []).append((model, generation))

        out: list[UpgradeRecommendation] = []
        for (family, _), entries in by_group.items():
            newest_gen = max(generation for _, generation in entries)
            newest_candidates = [
                model for model, generation in entries if generation >= newest_gen
            ]
            for model, generation in entries:
                if generation >= newest_gen:
                    continue
                best = self._best_candidate(
                    model, generation, newest_candidates, newest_gen
                )
                if best is None:
                    continue
                candidate, score = best
                out.append(
                    UpgradeRecommendation(
                        provider_name=provider_name,
                        current_model_id=model.id,
                        recommended_model_id=candidate.id,
                        family=family,
                        current_generation=generation,
                        recommended_generation=newest_gen,
                        score=score,
                        reason=(
                            f"Newer in-family model "
                            f"{flatten_label(candidate.id)!r} "
                            f"(generation {newest_gen}) available; current "
                            f"{flatten_label(model.id)!r} is "
                            f"generation {generation}."
                        ),
                    ),
                )
        return out
