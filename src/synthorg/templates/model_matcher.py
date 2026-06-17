"""Capability-aware model-matching engine.

Given a :class:`~synthorg.templates.model_requirements.ModelRequirement`
and a set of available provider models, selects the best-fit model by
(a) hard-filtering on declared capability requirements against each
model's persisted metadata, (b) resolving any family/pattern reference
to the newest matching configured model and pinning a concrete id, and
(c) scoring the survivors on an absolute capability / context / priority
composite.  Selection is pluggable via :class:`ModelSelectionStrategy`.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import date
from fnmatch import fnmatch
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from synthorg.config.schema import ProviderModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_MODEL_MATCH_COERCED,
    TEMPLATE_MODEL_MATCH_FAILED,
    TEMPLATE_MODEL_MATCH_FALLBACK,
    TEMPLATE_MODEL_MATCH_SKIPPED,
    TEMPLATE_MODEL_MATCH_SUCCESS,
)
from synthorg.templates.model_matcher_config import (
    _DEFAULT_MATCHER_CONFIG,
    ModelMatcherConfig,
    derive_tier,
)
from synthorg.templates.model_requirements import ModelRequirement, ModelTier

logger = get_logger(__name__)

# Number of known capability flags scored by capability-fit.
_CAPABILITY_COUNT: Final[int] = 3

# Latency stand-in (ms) for models without a measured latency, so they
# sort last on the speed axis without using inf (frozen models forbid it).
_LATENCY_UNKNOWN_MS: Final[int] = 10_000_000

# Weight of the (pool-normalised) generation axis in the balanced blend;
# the remainder weights cheapness. 0.5 splits quality and cost evenly.
_BALANCED_GENERATION_WEIGHT: Final[float] = 0.5


class _ProviderWithModels(Protocol):
    """Structural type for a provider config exposing its models."""

    models: tuple[ProviderModelConfig, ...]


class ModelMatch(BaseModel):
    """Result of matching a single agent to a provider model.

    Attributes:
        agent_index: Index of the agent in the template agent list.
        provider_name: Name of the matched provider.
        model_id: Matched (pinned) model identifier.
        tier: Report-only tier derived from the selected model's metadata.
        score: Match quality score (higher is better, 0-1 range).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    agent_index: int = Field(ge=0)
    provider_name: NotBlankStr
    model_id: NotBlankStr
    tier: ModelTier
    score: float = Field(ge=0.0, le=1.0)


@runtime_checkable
class ModelSelectionStrategy(Protocol):
    """Selects the best model for a requirement from candidates."""

    def select(
        self,
        requirement: ModelRequirement,
        candidates: Sequence[ProviderModelConfig],
        config: ModelMatcherConfig,
    ) -> tuple[ProviderModelConfig | None, float]:
        """Return the best model and its 0-1 score (or ``None``, 0.0)."""
        ...


class CapabilityFitStrategy:
    """Default capability-aware selection strategy.

    Phase A hard-filters on declared capability requirements (fail-closed
    against unknown metadata); phase B pins the newest model matching a
    family/pattern reference; phase C scores survivors on an absolute
    capability / context / priority composite.
    """

    def select(
        self,
        requirement: ModelRequirement,
        candidates: Sequence[ProviderModelConfig],
        config: ModelMatcherConfig,
    ) -> tuple[ProviderModelConfig | None, float]:
        """Select the best model for *requirement* from *candidates*.

        Returns:
            ``(model, score)`` for the best survivor, or ``(None, 0.0)``
            when nothing clears the hard filters.
        """
        survivors = [m for m in candidates if self._passes_hard_filters(m, requirement)]
        if not survivors:
            return None, 0.0

        if requirement.family is not None or requirement.model_pattern is not None:
            matched = [m for m in survivors if self._ref_matches(m, requirement)]
            if matched:
                best = self._newest(matched)
                return best, self._score(best, requirement, survivors, config)
            logger.debug(
                TEMPLATE_MODEL_MATCH_FALLBACK,
                reason="family_pattern_miss",
                family=requirement.family,
                pattern=requirement.model_pattern,
            )

        scored = [
            (m, self._score(m, requirement, survivors, config)) for m in survivors
        ]
        return max(scored, key=lambda pair: pair[1])

    def _passes_hard_filters(
        self,
        model: ProviderModelConfig,
        requirement: ModelRequirement,
    ) -> bool:
        """Return ``True`` when *model* clears every hard requirement.

        Fail-closed: a required capability against ``unknown`` metadata is
        a hard fail (we cannot prove the capability is present).
        """
        if model.max_context < requirement.min_context:
            return False
        meta = model.metadata
        unknown = meta.metadata_source == "unknown"
        required_checks = (
            (requirement.requires_tools, meta.supports_tools),
            (requirement.requires_vision, meta.supports_vision),
            (requirement.requires_reasoning, meta.supports_reasoning),
        )
        for required, supported in required_checks:
            if required and (unknown or not supported):
                logger.debug(
                    TEMPLATE_MODEL_MATCH_SKIPPED,
                    model=model.id,
                    reason="capability_unmet",
                    metadata_unknown=unknown,
                )
                return False
        return True

    def _ref_matches(
        self,
        model: ProviderModelConfig,
        requirement: ModelRequirement,
    ) -> bool:
        """Return ``True`` when *model* matches the family/pattern ref."""
        family = requirement.family
        if family is not None:
            stored = (model.metadata.family or "").lower()
            if stored and stored == family.strip().lower():
                return True
        pattern = requirement.model_pattern
        return pattern is not None and fnmatch(model.id, pattern)

    def _newest(
        self,
        models: Sequence[ProviderModelConfig],
    ) -> ProviderModelConfig:
        """Return the newest model by (generation, release_date, id)."""
        return max(
            models,
            key=lambda m: (
                m.metadata.generation if m.metadata.generation is not None else -1.0,
                m.metadata.release_date or date.min,
                m.id,
            ),
        )

    def _score(
        self,
        model: ProviderModelConfig,
        requirement: ModelRequirement,
        pool: Sequence[ProviderModelConfig],
        config: ModelMatcherConfig,
    ) -> float:
        """Compute a 0-1 score for *model* within *pool*.

        Returns:
            ``base_score`` plus capability-fit, context-headroom, and
            priority bonuses, capped at 1.0.
        """
        score = config.base_score
        score += self._capability_fit(model, config)
        score += self._headroom_bonus(model, requirement, config)
        score += self._priority_bonus(model, pool, requirement.priority, config)
        return min(1.0, score)

    def _capability_fit(
        self,
        model: ProviderModelConfig,
        config: ModelMatcherConfig,
    ) -> float:
        """Bonus for the fraction of known capabilities the model has.

        Returns:
            ``capability_fit_weight`` scaled by the share of supported
            capability flags.
        """
        meta = model.metadata
        present = (
            int(meta.supports_tools)
            + int(meta.supports_vision)
            + int(meta.supports_reasoning)
        )
        return config.capability_fit_weight * present / _CAPABILITY_COUNT

    def _headroom_bonus(
        self,
        model: ProviderModelConfig,
        requirement: ModelRequirement,
        config: ModelMatcherConfig,
    ) -> float:
        """Bonus for context window exceeding the requirement (clamped).

        Returns:
            ``headroom_max_bonus`` scaled by the clamped context-headroom
            ratio (full bonus when no minimum is requested).
        """
        if requirement.min_context <= 0:
            return config.headroom_max_bonus
        headroom = model.max_context / requirement.min_context
        return (
            config.headroom_max_bonus
            * min(headroom, config.headroom_ratio_cap)
            / config.headroom_ratio_cap
        )

    def _priority_bonus(
        self,
        model: ProviderModelConfig,
        pool: Sequence[ProviderModelConfig],
        priority: str,
        config: ModelMatcherConfig,
    ) -> float:
        """Absolute-axis priority bonus: best in *pool* gets the full bonus.

        Scales by the model's value relative to the pool's min and max on
        the priority axis (not its rank): any model tied for best earns the
        full bonus, and the result is independent of how many ties exist, so
        an identical model scores the same regardless of pool composition.

        Returns:
            ``priority_max_bonus`` scaled by the model's value relative to
            the min and max values in *pool*.
        """
        if len(pool) <= 1:
            return config.priority_max_bonus
        value_of = _priority_ranker(pool, priority)
        values = [value_of(m) for m in pool]
        val_min, val_max = min(values), max(values)
        span = val_max - val_min
        if span <= 0.0:
            return config.priority_max_bonus
        return config.priority_max_bonus * (value_of(model) - val_min) / span


def _model_generation(model: ProviderModelConfig) -> float:
    """Return the model's generation, or ``0.0`` when unknown.

    Returns:
        The parsed ``metadata.generation`` or ``0.0``.
    """
    return model.metadata.generation if model.metadata.generation is not None else 0.0


def _priority_ranker(
    pool: Sequence[ProviderModelConfig],
    priority: str,
) -> Callable[[ProviderModelConfig], float]:
    """Build a higher-is-better value function for *priority* over *pool*.

    For ``balanced`` the generation and cost axes are normalised to
    ``[0, 1]`` within *pool* before blending, so the two incomparable
    scales contribute evenly instead of generation dominating.

    Returns:
        A callable mapping a model to its priority-axis value.
    """
    if priority != "balanced":
        return lambda m: _priority_value(m, priority)

    gens = [_model_generation(m) for m in pool]
    costs = [m.cost_per_1k_input for m in pool]
    gen_min, gen_span = min(gens), (max(gens) - min(gens)) or 1.0
    cost_min, cost_span = min(costs), (max(costs) - min(costs)) or 1.0

    def balanced(model: ProviderModelConfig) -> float:
        norm_gen = (_model_generation(model) - gen_min) / gen_span
        norm_cost = (model.cost_per_1k_input - cost_min) / cost_span
        return _BALANCED_GENERATION_WEIGHT * norm_gen + (
            1.0 - _BALANCED_GENERATION_WEIGHT
        ) * (1.0 - norm_cost)

    return balanced


def _priority_value(model: ProviderModelConfig, priority: str) -> float:
    """Higher-is-better value of *model* on a single (non-balanced) axis.

    Returns:
        ``generation`` for quality (and as the default), negative cost
        for cost, and negative latency for speed.
    """
    if priority == "cost":
        return -model.cost_per_1k_input
    if priority == "speed":
        latency = model.estimated_latency_ms
        return -float(latency if latency is not None else _LATENCY_UNKNOWN_MS)
    return _model_generation(model)


_DEFAULT_STRATEGY: ModelSelectionStrategy = CapabilityFitStrategy()


def get_model_selection_strategy() -> ModelSelectionStrategy:
    """Return the default model-selection strategy singleton.

    Returns:
        The shared :class:`CapabilityFitStrategy`; swap by passing a
        ``strategy`` to :func:`match_model` / :func:`match_all_agents`.
    """
    return _DEFAULT_STRATEGY


def match_model(
    requirement: ModelRequirement,
    available: tuple[ProviderModelConfig, ...],
    matcher_config: ModelMatcherConfig | None = None,
    strategy: ModelSelectionStrategy | None = None,
) -> tuple[ProviderModelConfig | None, float]:
    """Select the best model for a requirement from available models.

    Args:
        requirement: Structured model requirement.
        available: Tuple of available models from a single provider.
        matcher_config: Operator-tunable score weights. ``None`` falls
            back to the default projected from ``EngineBridgeConfig``.
        strategy: Selection strategy. ``None`` uses the default
            :class:`CapabilityFitStrategy`.

    Returns:
        Tuple of (best matching model or None, score 0-1).
    """
    if not available:
        return None, 0.0
    cfg = matcher_config if matcher_config is not None else _DEFAULT_MATCHER_CONFIG
    selector = strategy if strategy is not None else _DEFAULT_STRATEGY
    return selector.select(requirement, available, cfg)


def _resolve_agent_requirement(
    agent: Mapping[str, object],
    idx: int,
    model_requirement_cls: type[ModelRequirement],
    parse_fn: Callable[[str | dict[str, JsonValue]], ModelRequirement],
    resolve_fn: Callable[[str, str | None], ModelRequirement],
) -> ModelRequirement | None:
    """Resolve a single agent's model requirement.

    Returns:
        The resolved ``ModelRequirement``, or ``None`` if the agent
        should be skipped (invalid requirement logged as warning).
    """
    model_req = agent.get("model_requirement")
    if isinstance(model_req, model_requirement_cls):
        return model_req

    if isinstance(model_req, dict):
        try:
            return parse_fn(model_req)
        except (ValidationError, ValueError) as exc:
            logger.warning(
                TEMPLATE_MODEL_MATCH_SKIPPED,
                agent_index=idx,
                reason="invalid_model_requirement_dict",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    raw_tier = agent.get("tier", "medium")
    if isinstance(raw_tier, str):
        tier_str = raw_tier
    else:
        logger.warning(
            TEMPLATE_MODEL_MATCH_COERCED,
            agent_index=idx,
            field="tier",
            coerced_to="medium",
            value_type=type(raw_tier).__name__,
        )
        tier_str = "medium"
    raw_preset = agent.get("personality_preset")
    if raw_preset is None or isinstance(raw_preset, str):
        preset = raw_preset
    else:
        logger.warning(
            TEMPLATE_MODEL_MATCH_COERCED,
            agent_index=idx,
            field="personality_preset",
            coerced_to=None,
            value_type=type(raw_preset).__name__,
        )
        preset = None
    try:
        return resolve_fn(tier_str, preset)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            TEMPLATE_MODEL_MATCH_SKIPPED,
            agent_index=idx,
            tier=tier_str,
            preset=preset,
            reason="invalid_requirement",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


def match_all_agents(
    agents: Sequence[Mapping[str, object]],
    providers: Mapping[str, _ProviderWithModels],
    matcher_config: ModelMatcherConfig | None = None,
    strategy: ModelSelectionStrategy | None = None,
) -> list[ModelMatch]:
    """Batch-match template agents to provider models.

    For each agent, resolves its model requirement and finds the best
    model across all configured providers.  The reported ``tier`` is
    derived from the *selected* model's metadata.

    Args:
        agents: List of expanded agent config dicts. Requirement
            resolution checks ``model_requirement`` (object then dict),
            then ``tier`` + optional ``personality_preset``.
        providers: Provider name -> provider config mapping; each must
            expose a ``models`` tuple of ``ProviderModelConfig``.
        matcher_config: Operator-tunable score weights. ``None`` uses the
            default projected from ``EngineBridgeConfig``.
        strategy: Selection strategy. ``None`` uses the default.

    Returns:
        List of ``ModelMatch`` results. An agent is omitted when no
        model clears its hard capability requirements (fail-closed),
        when no models exist anywhere, or when requirement resolution
        fails. Callers handle an omitted agent (left unassigned at setup).
    """
    from synthorg.templates.model_requirements import (  # noqa: PLC0415
        ModelRequirement,
        parse_model_requirement,
        resolve_model_requirement,
    )

    cfg = matcher_config if matcher_config is not None else _DEFAULT_MATCHER_CONFIG
    results: list[ModelMatch] = []

    for idx, agent in enumerate(agents):
        req = _resolve_agent_requirement(
            agent,
            idx,
            ModelRequirement,
            parse_model_requirement,
            resolve_model_requirement,
        )
        if req is None:
            continue
        match = _match_agent(idx, req, providers, cfg, strategy)
        if match is not None:
            results.append(match)

    return results


def _match_agent(
    idx: int,
    req: ModelRequirement,
    providers: Mapping[str, _ProviderWithModels],
    cfg: ModelMatcherConfig,
    strategy: ModelSelectionStrategy | None,
) -> ModelMatch | None:
    """Find the best model for one resolved requirement across providers.

    Fail-closed: when no model clears the requirement's hard capability
    filters in any provider, returns ``None`` rather than assigning a
    non-compliant model. The caller leaves such an agent unassigned.

    Returns:
        The best ``ModelMatch`` across all providers, or ``None`` when no
        model satisfies the hard capability requirements.
    """
    best_provider: str | None = None
    best_model: ProviderModelConfig | None = None
    best_score = 0.0
    for pname, pcfg in providers.items():
        model, score = match_model(req, pcfg.models, cfg, strategy)
        if model is not None and (best_model is None or score > best_score):
            best_provider, best_model, best_score = pname, model, score

    if best_provider is not None and best_model is not None:
        logger.debug(
            TEMPLATE_MODEL_MATCH_SUCCESS,
            agent_index=idx,
            provider=best_provider,
            model=best_model.id,
            score=best_score,
        )
        return ModelMatch(
            agent_index=idx,
            provider_name=best_provider,
            model_id=best_model.id,
            tier=derive_tier(best_model, cfg),
            score=best_score,
        )
    logger.debug(
        TEMPLATE_MODEL_MATCH_FAILED,
        agent_index=idx,
        reason="no_compliant_model",
    )
    return None
