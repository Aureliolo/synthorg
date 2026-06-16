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
from synthorg.settings.bridge_configs import EngineBridgeConfig
from synthorg.templates.model_requirements import ModelRequirement, ModelTier

logger = get_logger(__name__)

# Number of known capability flags scored by capability-fit.
_CAPABILITY_COUNT: Final[int] = 3

# Latency stand-in (ms) for models without a measured latency, so they
# sort last on the speed axis without using inf (frozen models forbid it).
_LATENCY_UNKNOWN_MS: Final[int] = 10_000_000


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


class ModelMatcherConfig(BaseModel):
    """Operator-tunable weights for the capability-aware matcher.

    The score of a surviving candidate is ``base_score`` plus the
    capability-fit, context-headroom, and priority bonuses, capped at
    1.0.  ``tier_*_min_context`` derive the report-only tier label.

    Field defaults mirror the registered defaults in
    :mod:`synthorg.settings.definitions.engine`. Runtime callers passing
    ``matcher_config=None`` fall back to ``_DEFAULT_MATCHER_CONFIG``,
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
    from synthorg.settings.bridge_configs import EngineBridgeConfig  # noqa: PLC0415

    return ModelMatcherConfig.from_bridge_config(EngineBridgeConfig())


_DEFAULT_MATCHER_CONFIG = _build_default_matcher_config()


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

        best = max(
            survivors,
            key=lambda m: self._score(m, requirement, survivors, config),
        )
        return best, self._score(best, requirement, survivors, config)

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
        if family is not None and model.metadata.family == family.strip().lower():
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

        Returns:
            ``priority_max_bonus`` scaled by the model's rank on the
            priority axis within *pool*.
        """
        if len(pool) <= 1:
            return config.priority_max_bonus
        value = _priority_value(model, priority)
        worse = sum(1 for m in pool if _priority_value(m, priority) < value)
        return config.priority_max_bonus * worse / (len(pool) - 1)


def _priority_value(model: ProviderModelConfig, priority: str) -> float:
    """Higher-is-better value of *model* on the *priority* axis.

    Returns:
        ``generation`` for quality, negative cost for cost, negative
        latency for speed, and a generation-minus-cost blend for balanced.
    """
    cost = model.cost_per_1k_input
    gen = model.metadata.generation if model.metadata.generation is not None else 0.0
    if priority == "quality":
        return gen
    if priority == "cost":
        return -cost
    if priority == "speed":
        latency = model.estimated_latency_ms
        return -float(latency if latency is not None else _LATENCY_UNKNOWN_MS)
    return gen - cost


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
        List of ``ModelMatch`` results. Agents are omitted only when no
        models exist anywhere or requirement resolution fails; an agent
        with models but no capability match falls back to the first
        available model with score 0.
    """
    from synthorg.templates.model_requirements import (  # noqa: PLC0415
        ModelRequirement,
        parse_model_requirement,
        resolve_model_requirement,
    )

    cfg = matcher_config if matcher_config is not None else _DEFAULT_MATCHER_CONFIG
    results: list[ModelMatch] = []

    all_models: list[tuple[str, ProviderModelConfig]] = [
        (pname, m) for pname, pcfg in providers.items() for m in pcfg.models
    ]

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

        best_provider: str | None = None
        best_model: ProviderModelConfig | None = None
        best_score = 0.0

        for pname, pcfg in providers.items():
            model, score = match_model(req, pcfg.models, cfg, strategy)
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
                    tier=derive_tier(best_model, cfg),
                    score=best_score,
                ),
            )
        elif all_models:
            fb_provider, fb_model = all_models[0]
            logger.debug(
                TEMPLATE_MODEL_MATCH_FALLBACK,
                agent_index=idx,
                fallback_provider=fb_provider,
                fallback_model=fb_model.id,
            )
            results.append(
                ModelMatch(
                    agent_index=idx,
                    provider_name=fb_provider,
                    model_id=fb_model.id,
                    tier=derive_tier(fb_model, cfg),
                    score=0.0,
                ),
            )
        else:
            logger.warning(
                TEMPLATE_MODEL_MATCH_FAILED,
                agent_index=idx,
                reason="no_models_available",
            )

    return results
