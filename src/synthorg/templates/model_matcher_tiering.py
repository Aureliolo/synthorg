# module-kind: code
"""Capability-demand model selection with domination pruning + family spread.

The model an agent gets is driven by **how hard its work is**, not its org
rank: a role's declared demand (``priority`` + ``requires_*``) maps to a cost
tier, and the agent draws from that tier. Hard, reasoning-heavy work pulls the
heavy models; routine work the light ones -- which on ollama (where bigger =
both more capable and more quota-hungry) is also the cost-optimal allocation.

Two refinements keep the result clean:

* **Domination pruning** -- within one cost tier, an older model of the same
  family is strictly dominated by its newer sibling (same price, worse), so it
  is dropped. The matcher never assigns ``glm-4.7`` when ``glm-5.2`` is the
  same tier, nor ``kimi-k2.5``/``k2.6`` when ``k2.7`` exists.
* **Family spread** -- among equally-suitable models in a tier, the least-used
  family wins, so a roster fans out across model lines instead of stacking.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

from synthorg.config.schema import ProviderModelConfig
from synthorg.templates.model_requirements import ModelRequirement

_TIER_QUALITY_REASONING: Final[int] = 4
_TIER_HIGH: Final[int] = 3
_TIER_BALANCED: Final[int] = 2
_TIER_ECONOMICAL: Final[int] = 1

# Curated tier overrides keyed by model id (preferred) or family: promote or
# demote a model across cost tiers, overriding its scraped usage tier in both
# directions. A model whose ollama usage tier understates its real capability
# -- glm-5.2 is priced tier-3 on quota yet ranks top-tier on reasoning
# benchmarks -- is promoted so the matcher reaches for it on the hardest work;
# the same hook demotes a known-weak model. Operator-tunable via
# ``ModelMatcherConfig.tier_overrides``; lives here (not a settings scalar) so
# the curated default ships with the matcher.
DEFAULT_TIER_OVERRIDES: Final[Mapping[str, int]] = MappingProxyType(
    {"glm-5.2": _TIER_QUALITY_REASONING},
)
_NO_TIER_OVERRIDES: Final[Mapping[str, int]] = MappingProxyType({})

# Capability floor: within one cost tier, family-spread must not pick a model
# far weaker than the tier's strongest (a tiny model is poor value at the same
# price). Only models at least this fraction of the band's best size compete
# for the spread, so a 1B never beats a same-tier 26B just for variety.
_SPREAD_MIN_QUALITY_FRACTION: Final[float] = 0.125

# Family-spread discount applied to a curated-PROMOTED model (one whose
# override sits above its scraped cost tier -- cheap quota, top capability,
# e.g. glm-5.2). It shaves a fraction off the model's effective family-usage so
# such a model is chosen a little MORE often without overriding family-spread:
# at 0.5 a promoted family beats an equally-used rival family but still yields
# to a strictly-less-used one, so the roster never collapses to all-one-model.
_PROMOTION_SPREAD_BONUS: Final[float] = 0.5


def demand_tier(requirement: ModelRequirement) -> int:
    """Map a role's declared capability demand to a target cost tier (1-4).

    The signal is the work's difficulty as the template declares it, not org
    seniority: reasoning-heavy quality work draws the heaviest tier, routine
    cost/speed work the lightest.

    Returns:
        A 1-4 target tier.
    """
    if requirement.priority == "quality" and requirement.requires_reasoning:
        return _TIER_QUALITY_REASONING
    if requirement.priority == "quality" or requirement.requires_reasoning:
        return _TIER_HIGH
    if requirement.priority in ("cost", "speed"):
        return _TIER_ECONOMICAL
    return _TIER_BALANCED


def _capability_count(model: ProviderModelConfig) -> int:
    """Return how many capability flags a model declares.

    Returns:
        Count of supported capabilities (tools/vision/reasoning).
    """
    meta = model.metadata
    return sum((meta.supports_tools, meta.supports_vision, meta.supports_reasoning))


def _quality_key(model: ProviderModelConfig) -> tuple[float, float, int, int]:
    """Sort key ranking a model by capability strength (higher is stronger).

    Returns:
        ``(generation, parameter_count, capability_count, max_context)`` with
        missing values treated as zero, so a newer / larger / more-capable
        model sorts ahead.
    """
    meta = model.metadata
    return (
        meta.generation if meta.generation is not None else 0.0,
        float(meta.parameter_count) if meta.parameter_count is not None else 0.0,
        _capability_count(model),
        model.max_context,
    )


def rank_by_quality(
    models: Sequence[ProviderModelConfig],
) -> list[ProviderModelConfig]:
    """Return *models* sorted strongest-first by the quality key.

    Returns:
        A new list ordered by descending strength.
    """
    return sorted(models, key=_quality_key, reverse=True)


def _tier_override(
    model: ProviderModelConfig,
    overrides: Mapping[str, int],
) -> int | None:
    """Return the configured tier override for a model, if any.

    Returns:
        The override tier (by id, then family), or ``None`` when unset.
    """
    by_id = overrides.get(model.id)
    if by_id is not None:
        return by_id
    family = model.metadata.family
    return overrides.get(family) if family is not None else None


def _model_tiers(
    model: ProviderModelConfig,
    overrides: Mapping[str, int] = _NO_TIER_OVERRIDES,
) -> tuple[int, ...]:
    """Resolve every cost tier a model competes in.

    A *promotion* (override strictly above the scraped cost tier) is ADDITIVE:
    the model competes in both its cheap scraped tier and its promoted tier, so
    a cheap-but-capable model (glm-5.2: tier-3 quota, tier-4 capability) is
    reachable both as value and for the hardest work. A *demotion* (override at
    or below the scraped tier) REPLACES, so a known-weak model never re-enters
    its inflated scraped tier. A model with neither a scraped tier nor an
    override is uncomparable and yields an empty tuple.

    Returns:
        The tiers (1-4) the model competes in: ``()`` when uncomparable, one
        tier for a plain / demoted / override-only model, two for a promotion.
    """
    scraped = model.metadata.cost_tier
    override = _tier_override(model, overrides)
    if override is None:
        return (scraped,) if scraped is not None else ()
    if scraped is None or override <= scraped:
        return (override,)
    return (scraped, override)


def _is_promoted(
    model: ProviderModelConfig,
    overrides: Mapping[str, int] = _NO_TIER_OVERRIDES,
) -> bool:
    """Whether a curated override promotes the model above its scraped tier.

    Returns:
        ``True`` only for a genuine promotion (cheap quota, higher capability);
        ``False`` for a plain model, a demotion, or an override with no scraped
        baseline to promote from.
    """
    scraped = model.metadata.cost_tier
    if scraped is None:
        return False
    override = _tier_override(model, overrides)
    return override is not None and override > scraped


def prune_dominated(
    models: Sequence[ProviderModelConfig],
    overrides: Mapping[str, int] = _NO_TIER_OVERRIDES,
) -> list[ProviderModelConfig]:
    """Drop models dominated within their (cost tier, family) group.

    Two models of the same family in the same cost tier cost the same to run,
    so only the stronger (newer/larger/more-capable) one is ever worth using.
    Grouping honours *overrides* via :func:`_model_tiers`, so a PROMOTED model
    competes (and can win) in both its scraped and its promoted tier, while a
    demoted model is judged only in its lowered tier. Models without a
    resolvable tier or family pass through untouched (they cannot be safely
    compared).

    Returns:
        The surviving models (best-per-(tier, family) plus the unclassifiable),
        each appearing once even when it wins several groups.
    """
    best_by_group: dict[tuple[int, str], ProviderModelConfig] = {}
    passthrough: list[ProviderModelConfig] = []
    for model in models:
        family = model.metadata.family
        tiers = _model_tiers(model, overrides)
        if family is None or not tiers:
            passthrough.append(model)
            continue
        for tier in tiers:
            group = (tier, family)
            incumbent = best_by_group.get(group)
            if incumbent is None or _quality_key(model) > _quality_key(incumbent):
                best_by_group[group] = model
    # A promoted model can win several (tier, family) groups; dedup by identity
    # (first insertion wins, preserving order) so it appears exactly once.
    survivors = list({id(m): m for m in best_by_group.values()}.values())
    return [*survivors, *passthrough]


def select_for_demand(
    eligible: Sequence[ProviderModelConfig],
    target_tier: int,
    family_usage: Counter[str],
    overrides: Mapping[str, int] = _NO_TIER_OVERRIDES,
) -> ProviderModelConfig | None:
    """Pick a model nearest the target cost tier, spreading across families.

    Chooses from the models whose tier is closest to *target_tier* (the best
    achievable when nothing sits exactly at it). A promoted model is near both
    its scraped and its promoted tier (:func:`_model_tiers`), so it competes
    for either. Among the nearest band, the least-used family fans the roster
    out; a promoted model carries a small spread discount so it is picked a
    little more often without overriding spread, and ties resolve to the
    stronger model.

    Args:
        eligible: Hard-filter-passing, domination-pruned candidates.
        target_tier: The role's demand tier (1-4).
        family_usage: Running per-family assignment counts, updated by caller.
        overrides: Curated tier overrides applied to each model's tier.

    Returns:
        The chosen model, or ``None`` when nothing is eligible.
    """
    if not eligible:
        return None

    def _distance(model: ProviderModelConfig) -> int:
        tiers = _model_tiers(model, overrides) or (_TIER_BALANCED,)
        return min(abs(tier - target_tier) for tier in tiers)

    distance = {id(m): _distance(m) for m in eligible}
    nearest = min(distance.values())
    band = rank_by_quality([m for m in eligible if distance[id(m)] == nearest])
    spread_pool = _within_quality_floor(band)

    def _spread_key(i: int) -> tuple[float, int]:
        model = spread_pool[i]
        used = float(family_usage[model.metadata.family or model.id])
        bonus = _PROMOTION_SPREAD_BONUS if _is_promoted(model, overrides) else 0.0
        return (used - bonus, i)

    return spread_pool[min(range(len(spread_pool)), key=_spread_key)]


def _within_quality_floor(
    band: Sequence[ProviderModelConfig],
) -> list[ProviderModelConfig]:
    """Restrict spread candidates to models near the band's top capability.

    Returns:
        Models whose parameter count is at least the floor fraction of the
        band's largest (size-unknown models kept), so family-spread cannot
        pick a far-weaker model at the same cost. Falls back to the full band
        when nothing qualifies.
    """
    best_params = max((m.metadata.parameter_count or 0 for m in band), default=0)
    if best_params <= 0:
        return list(band)
    floor = best_params * _SPREAD_MIN_QUALITY_FRACTION
    kept = [
        m
        for m in band
        if m.metadata.parameter_count is None or m.metadata.parameter_count >= floor
    ]
    return kept or list(band)
