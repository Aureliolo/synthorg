"""Tier-to-model matching engine.

Given a :class:`~synthorg.templates.model_requirements.ModelRequirement`
and a set of available provider models, selects the best-fit model by
classifying models into cost-based tiers and ranking within each tier
according to the requirement's priority axis.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_MODEL_MATCH_FAILED,
    TEMPLATE_MODEL_MATCH_FALLBACK,
    TEMPLATE_MODEL_MATCH_SKIPPED,
    TEMPLATE_MODEL_MATCH_SUCCESS,
)
from synthorg.templates.model_requirements import ModelTier  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.config.schema import ProviderModelConfig
    from synthorg.templates.model_requirements import ModelRequirement

logger = get_logger(__name__)


class ModelMatch(BaseModel):
    """Result of matching a single agent to a provider model.

    Attributes:
        agent_index: Index of the agent in the template agent list.
        provider_name: Name of the matched provider.
        model_id: Matched model identifier.
        tier: Original tier requirement from the template.
        score: Match quality score (higher is better, 0-1 range).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    agent_index: int = Field(ge=0)
    provider_name: NotBlankStr
    model_id: NotBlankStr
    tier: ModelTier
    score: float = Field(ge=0.0, le=1.0)


def match_model(
    requirement: ModelRequirement,
    available: tuple[ProviderModelConfig, ...],
) -> tuple[ProviderModelConfig | None, float]:
    """Select the best model for a requirement from available models.

    Models are classified into cost-based tiers (thirds by input cost),
    then ranked within the matching tier according to the requirement's
    priority axis.

    Args:
        requirement: Structured model requirement.
        available: Tuple of available models from a single provider.

    Returns:
        Tuple of (best matching model or None, score 0-1).
    """
    if not available:
        return None, 0.0

    # Filter by minimum context window.
    candidates = [m for m in available if m.max_context >= requirement.min_context]
    if not candidates:
        return None, 0.0

    # Classify into tiers by cost.
    tier_models = _classify_tiers(candidates)
    tier_candidates = tier_models.get(requirement.tier, [])

    # Fall back to next-best tier if exact tier is empty.
    if not tier_candidates:
        for fallback in _TIER_FALLBACK[requirement.tier]:
            tier_candidates = tier_models.get(fallback, [])
            if tier_candidates:
                break

    if not tier_candidates:
        return None, 0.0

    # Rank within tier by priority axis.
    best = _rank_by_priority(tier_candidates, requirement.priority)
    score = _compute_score(best, requirement, tier_candidates)
    return best, score


def _resolve_agent_requirement(
    agent: dict[str, Any],
    idx: int,
    model_requirement_cls: type,
    parse_fn: Any,
    resolve_fn: Any,
) -> tuple[Any, ModelTier] | None:
    """Resolve a single agent's model requirement.

    Returns:
        ``(requirement, tier)`` on success, or ``None`` if the agent
        should be skipped (invalid requirement logged as warning).
    """
    model_req = agent.get("model_requirement")
    if isinstance(model_req, model_requirement_cls):
        return model_req, model_req.tier  # type: ignore[attr-defined]

    if isinstance(model_req, dict):
        try:
            req = parse_fn(model_req)
        except (ValidationError, ValueError) as exc:
            logger.warning(
                TEMPLATE_MODEL_MATCH_SKIPPED,
                agent_index=idx,
                reason="invalid_model_requirement_dict",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        return req, req.tier

    tier: ModelTier = agent.get("tier", "medium")
    preset = agent.get("personality_preset")
    try:
        req = resolve_fn(tier, preset)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            TEMPLATE_MODEL_MATCH_SKIPPED,
            agent_index=idx,
            tier=tier,
            preset=preset,
            reason="invalid_requirement",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    return req, tier


def match_all_agents(
    agents: list[dict[str, Any]],
    providers: Mapping[str, Any],
) -> list[ModelMatch]:
    """Batch-match template agents to provider models.

    For each agent, resolves its model requirement and finds the best
    model across all configured providers.

    Note:
        The *agents* list is shallow-copied from the caller. Each dict
        is shared, so nested mutable values (e.g. ``personality``) are
        **not** copied. This function only reads agent dicts.

    Args:
        agents: List of expanded agent config dicts.  Model requirement
            resolution uses three paths (checked in order):

            - ``model_requirement`` (``ModelRequirement``): used directly.
            - ``model_requirement`` (dict): deserialized to
              ``ModelRequirement`` via ``parse_model_requirement``.
            - ``tier`` (str) + optional ``personality_preset`` (str):
              resolved via ``resolve_model_requirement`` with
              personality-based affinity defaults.
        providers: Provider name -> provider config mapping.  Each
            provider config must have a ``models`` attribute returning
            a tuple of ``ProviderModelConfig``.

    Returns:
        List of ``ModelMatch`` results.  Agents may be omitted from
        the result when no models exist across any provider or when
        requirement resolution fails.  Agents with a viable provider
        but no tier match get a ``ModelMatch`` with score 0 and the
        first available provider/model as a fallback.
    """
    from synthorg.templates.model_requirements import (  # noqa: PLC0415
        ModelRequirement,
        parse_model_requirement,
        resolve_model_requirement,
    )

    results: list[ModelMatch] = []

    # Flatten all models across providers for fallback.
    all_models: list[tuple[str, ProviderModelConfig]] = [
        (pname, m) for pname, pcfg in providers.items() for m in pcfg.models
    ]

    for idx, agent in enumerate(agents):
        resolved = _resolve_agent_requirement(
            agent,
            idx,
            ModelRequirement,
            parse_model_requirement,
            resolve_model_requirement,
        )
        if resolved is None:
            continue
        req, tier = resolved

        best_provider: str | None = None
        best_model: ProviderModelConfig | None = None
        best_score = 0.0

        # Try each provider.
        for pname, pcfg in providers.items():
            model, score = match_model(req, pcfg.models)
            if model is not None and score > best_score:
                best_provider = pname
                best_model = model
                best_score = score

        if best_provider is not None and best_model is not None:
            logger.debug(
                TEMPLATE_MODEL_MATCH_SUCCESS,
                agent_index=idx,
                provider=best_provider,
                model=best_model.id,
                score=best_score,
            )
            results.append(
                ModelMatch(
                    agent_index=idx,
                    provider_name=best_provider,
                    model_id=best_model.id,
                    tier=tier,
                    score=best_score,
                ),
            )
        elif all_models:
            # Fallback: assign first available model with score 0.
            # This path IS the documented contract for tier-mismatch
            # (per docs/design/agents.md §"Model matcher"); the
            # fallback succeeded so logging at WARNING produced
            # ~8 noisy lines per setup wizard run. Issue #1666 B-5
            # downgrades this to DEBUG. WARNING stays for the truly
            # failing path -- the ``no_models_available`` branch
            # below.
            fb_provider, fb_model = all_models[0]
            logger.debug(
                TEMPLATE_MODEL_MATCH_FALLBACK,
                agent_index=idx,
                tier=tier,
                fallback_provider=fb_provider,
                fallback_model=fb_model.id,
            )
            results.append(
                ModelMatch(
                    agent_index=idx,
                    provider_name=fb_provider,
                    model_id=fb_model.id,
                    tier=tier,
                    score=0.0,
                ),
            )
        else:
            logger.warning(
                TEMPLATE_MODEL_MATCH_FAILED,
                agent_index=idx,
                tier=tier,
                reason="no_models_available",
            )

    return results


# ── Internal helpers ─────────────────────────────────────────


# Minimum number of models required for meaningful tier classification.
_MIN_TIER_SIZE: int = 3

# Tier fallback order: if exact tier has no models, try these.
_TIER_FALLBACK: MappingProxyType[ModelTier, tuple[ModelTier, ...]] = MappingProxyType(
    {
        "large": ("medium", "small"),
        "medium": ("large", "small"),
        "small": ("medium", "large"),
    }
)


def _classify_tiers(
    models: list[ProviderModelConfig],
) -> dict[ModelTier, list[ProviderModelConfig]]:
    """Split models into cost-based thirds.

    Models are sorted by ``cost_per_1k_input`` ascending.  The bottom
    third is ``small``, middle third is ``medium``, top third is
    ``large``.  With fewer than 3 models, all tiers map to all models.
    """
    if len(models) < _MIN_TIER_SIZE:
        # Too few to meaningfully tier -- every tier gets all models.
        return {"large": list(models), "medium": list(models), "small": list(models)}

    sorted_models = sorted(models, key=lambda m: m.cost_per_1k_input)
    n = len(sorted_models)
    third = n // 3

    # With n >= 3, each slice is guaranteed non-empty:
    # small gets at least 1 element, medium gets at least 1,
    # large gets the remainder (at least 1).
    return {
        "small": sorted_models[:third],
        "medium": sorted_models[third : 2 * third],
        "large": sorted_models[2 * third :],
    }


def _rank_by_priority(
    models: list[ProviderModelConfig],
    priority: str,
) -> ProviderModelConfig:
    """Pick the best model in a tier according to priority axis.

    Axes:
        quality: Highest cost (proxy for capability).
        speed: Lowest estimated latency. Models with ``None`` latency
            sort last (treated as infinite).
        cost: Lowest cost.
        balanced: Closest to the midpoint of the cost range.

    Args:
        models: Non-empty list of candidate models within a tier.
        priority: One of ``quality``, ``speed``, ``cost``, ``balanced``.

    Returns:
        The single best model for the given priority.

    Raises:
        ValueError: If *models* is empty.
    """
    if not models:
        msg = "Cannot rank empty model list"
        raise ValueError(msg)
    if priority == "quality":
        return max(models, key=lambda m: m.cost_per_1k_input)
    if priority == "speed":
        return min(
            models,
            key=lambda m: (
                m.estimated_latency_ms
                if m.estimated_latency_ms is not None
                else float("inf")
            ),
        )
    if priority == "cost":
        return min(models, key=lambda m: m.cost_per_1k_input)
    # "balanced" -- prefer mid-range cost.
    costs = [m.cost_per_1k_input for m in models]
    mid = (max(costs) + min(costs)) / 2
    return min(models, key=lambda m: abs(m.cost_per_1k_input - mid))


def _compute_score(
    model: ProviderModelConfig,
    requirement: ModelRequirement,
    tier_candidates: list[ProviderModelConfig],
) -> float:
    """Compute a 0-1 quality score for a match.

    Factors: base score (0.5), context headroom (0.25), priority
    alignment (0.25).
    """
    score = 0.5  # Base score for being in the right tier.

    # Context headroom bonus.
    if requirement.min_context > 0:
        headroom = model.max_context / requirement.min_context
        score += min(0.25, 0.25 * min(headroom, 2.0) / 2.0)
    else:
        score += 0.25

    # Priority alignment bonus.
    if len(tier_candidates) <= 1:
        score += 0.25
    else:
        score += _priority_alignment_bonus(
            model,
            requirement.priority,
            tier_candidates,
        )

    return min(1.0, score)


def _priority_alignment_bonus(
    model: ProviderModelConfig,
    priority: str,
    tier_candidates: list[ProviderModelConfig],
) -> float:
    """Return 0-0.25 bonus based on priority alignment.

    Args:
        model: The matched model.
        priority: The requirement priority axis.
        tier_candidates: All models in the matched tier (len >= 2).

    Returns:
        Bonus score in the range [0, 0.25].
    """
    ranked = sorted(
        tier_candidates,
        key=lambda m: m.cost_per_1k_input,
    )
    rank_map = {id(m): r for r, m in enumerate(ranked)}
    model_rank = rank_map.get(id(model), 0)
    max_rank = len(ranked) - 1

    if priority == "quality":
        return 0.25 * (model_rank / max_rank)
    if priority == "cost":
        return 0.25 * (1 - model_rank / max_rank)
    if priority == "speed":
        # Rank by latency: lowest latency gets full bonus.
        latency_ranked = sorted(
            tier_candidates,
            key=lambda m: (
                m.estimated_latency_ms
                if m.estimated_latency_ms is not None
                else float("inf")
            ),
        )
        latency_map = {id(m): r for r, m in enumerate(latency_ranked)}
        latency_rank = latency_map.get(id(model), 0)
        return 0.25 * (1 - latency_rank / max_rank)
    # "balanced" -- partial credit.
    return 0.125
