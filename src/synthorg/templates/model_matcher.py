# module-kind: code
"""Capability-aware model-matching engine.

Given a :class:`~synthorg.templates.model_requirements.ModelRequirement`
and a set of available provider models, selects the best-fit model by
(a) hard-filtering on declared capability requirements against each
model's persisted metadata, (b) resolving any family/pattern reference
to the newest matching configured model and pinning a concrete id, and
(c) scoring the survivors on an absolute capability / context / priority
composite.  Selection is pluggable via :class:`ModelSelectionStrategy`.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatch
from typing import Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from synthorg.config.model_metadata import is_tool_capable
from synthorg.config.schema import ProviderModelConfig
from synthorg.core.types import NotBlankStr
from synthorg.core.url_locality import is_local_url
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.template import (
    TEMPLATE_MODEL_MATCH_FAILED,
    TEMPLATE_MODEL_MATCH_FALLBACK,
    TEMPLATE_MODEL_MATCH_SKIPPED,
    TEMPLATE_MODEL_MATCH_SUCCESS,
)
from synthorg.templates.model_matcher_config import (
    DEFAULT_MATCHER_CONFIG,
    ModelMatcherConfig,
    derive_capability,
)
from synthorg.templates.model_matcher_priority import priority_ranker, shift_priority
from synthorg.templates.model_matcher_tiering import (
    above_usable_floor,
    demand_tier,
    enforce_cloud_floor,
    passes_hard_filters,
    prune_dominated,
    select_for_demand,
)
from synthorg.templates.model_requirements import CapabilityLevel, ModelRequirement

logger = get_logger(__name__)

# Number of known capability flags scored by capability-fit.
_CAPABILITY_COUNT: Final[int] = 3


class _ProviderWithModels(Protocol):
    """Structural type for a provider config exposing its models + endpoint."""

    models: tuple[ProviderModelConfig, ...]
    base_url: str | None
    agent_eligible: bool


class ModelMatch(BaseModel):
    """Result of matching a single agent to a provider model.

    Attributes:
        agent_index: Index of the agent in the template agent list.
        provider_name: Name of the matched provider.
        model_id: Matched (pinned) model identifier.
        capability: Report-only rung derived from the selected model's
            metadata.
        score: Match quality score (higher is better, 0-1 range).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    agent_index: int = Field(ge=0)
    provider_name: NotBlankStr
    model_id: NotBlankStr
    capability: CapabilityLevel
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

    Phase A hard-filters on declared capability requirements (optimistic for
    un-probed metadata -- a required capability fails only when the model is
    *known* to lack it; see :func:`passes_hard_filters`); phase B pins the
    newest model matching a family/pattern reference; phase C scores survivors
    on an absolute capability / context / priority composite.
    """

    def select(
        self,
        requirement: ModelRequirement,
        candidates: Sequence[ProviderModelConfig],
        config: ModelMatcherConfig,
    ) -> tuple[ProviderModelConfig | None, float]:
        """Select the best model for *requirement* from *candidates*.

        Resolution order: an explicit ``model_id`` pin wins outright over the
        capability *preferences* (the operator chose it) but never over the
        tool-calling floor; then a ``family`` / ``model_pattern`` reference
        pins the newest hard-filter survivor; then capability scoring over
        those survivors.

        Returns:
            ``(model, score)`` for the best survivor, or ``(None, 0.0)``
            when nothing matches the pin / clears the hard filters.
        """
        if requirement.model_id is not None:
            return self._select_pinned(requirement.model_id, candidates)

        survivors = [m for m in candidates if passes_hard_filters(m, requirement)]
        if not survivors:
            return None, 0.0

        if requirement.family is not None or requirement.model_pattern is not None:
            matched = [m for m in survivors if self._ref_matches(m, requirement)]
            if matched:
                best = self._newest(matched)
                # Score against the matched subset, not all survivors: the
                # pool-normalised priority bonus would otherwise let unrelated
                # non-matching models shift the pinned model's score.
                return best, self._score(best, requirement, matched, config)
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

    def _select_pinned(
        self,
        model_id: str,
        candidates: Sequence[ProviderModelConfig],
    ) -> tuple[ProviderModelConfig | None, float]:
        """Resolve an explicit ``model_id`` pin against the tool-calling floor.

        A pin is an escape hatch from the capability preferences, not from the
        floor: every agent turn dispatches with tool definitions attached, so a
        model runtime-proven unable to call them can only emit prose and fails
        any task expecting an artifact. The floor is the optimistic
        :func:`is_tool_capable` rule, so only a model *known* to lack tool
        calling is refused -- and refusing loudly beats seeding an agent that
        cannot do its work.

        Args:
            model_id: The pinned id or alias.
            candidates: Models available across the pool.

        Returns:
            ``(model, 1.0)`` for the newest pinned match, or ``(None, 0.0)``
            when the pin names nothing or names only tool-incapable models.
        """
        named = [m for m in candidates if model_id in (m.id, m.alias)]
        capable = [m for m in named if is_tool_capable(m.metadata)]
        if not capable:
            if named:
                logger.warning(
                    TEMPLATE_MODEL_MATCH_SKIPPED,
                    model=model_id,
                    reason="pinned_model_cannot_call_tools",
                )
            return None, 0.0
        return self._newest(capable), 1.0

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
        value_of = priority_ranker(pool, priority)
        values = [value_of(m) for m in pool]
        val_min, val_max = min(values), max(values)
        span = val_max - val_min
        if span <= 0.0:
            return config.priority_max_bonus
        return config.priority_max_bonus * (value_of(model) - val_min) / span


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
    cfg = matcher_config if matcher_config is not None else DEFAULT_MATCHER_CONFIG
    selector = strategy if strategy is not None else _DEFAULT_STRATEGY
    return selector.select(requirement, available, cfg)


def _resolve_agent_requirement(
    agent: Mapping[str, object],
    idx: int,
    model_requirement_cls: type[ModelRequirement],
    parse_fn: Callable[[str | dict[str, JsonValue]], ModelRequirement],
    resolve_fn: Callable[[dict[str, JsonValue] | None], ModelRequirement],
) -> ModelRequirement | None:
    """Resolve a single agent's model requirement.

    Checks ``model_requirement`` (object then dict) first; otherwise falls
    back to the empty requirement, which the capability matcher scores
    against every configured model.

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

    try:
        return resolve_fn(None)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            TEMPLATE_MODEL_MATCH_SKIPPED,
            agent_index=idx,
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
    *,
    model_spend_profile: str = "balanced",
) -> list[ModelMatch]:
    """Batch-match template agents to provider models.

    For each agent, resolves its model requirement and finds the best
    model across all configured providers.  The reported ``tier`` is
    derived from the *selected* model's metadata.

    Args:
        agents: List of expanded agent config dicts. Requirement
            resolution checks ``model_requirement`` (object then dict),
            then falls back to the empty requirement.
        providers: Provider name -> provider config mapping; each must
            expose a ``models`` tuple of ``ProviderModelConfig``.
        matcher_config: Operator-tunable score weights. ``None`` uses the
            default projected from ``EngineBridgeConfig``.
        strategy: Selection strategy. ``None`` uses the default.
        model_spend_profile: Company model-spend profile ('economy' | 'balanced' |
            'premium') that nudges each agent's resolved priority one rung
            along the cost<->quality ladder before matching; 'balanced' is a
            no-op, so an unset profile leaves matching unchanged.

    Returns:
        List of ``ModelMatch`` results. An agent is omitted when no model
        clears its hard capability requirements (a model the agent's required
        capability is *known* to lack), when no models exist anywhere, or when
        requirement resolution fails. Callers handle an omitted agent (left
        unassigned at setup).
    """
    from synthorg.templates.model_requirements import (  # noqa: PLC0415
        ModelRequirement,
        parse_model_requirement,
        resolve_model_requirement,
    )

    cfg = matcher_config if matcher_config is not None else DEFAULT_MATCHER_CONFIG
    selector = strategy if strategy is not None else _DEFAULT_STRATEGY
    pool, owner, local_ids = _build_pool(providers)
    # Domination pruning: drop the older sibling when a same-family model in
    # the same cost tier is strictly stronger (same price, worse). Tier
    # overrides apply so a promoted model is compared in its promoted tier.
    pruned = tuple(prune_dominated(pool, cfg.tier_overrides))
    ctx = _MatchContext(pruned, owner, local_ids, Counter(), cfg, selector)

    resolved: list[tuple[int, ModelRequirement]] = []
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
        # The company model-spend profile biases the whole roster cheaper
        # ('economy') or stronger ('premium') by nudging each agent's resolved
        # priority one rung along the cost<->quality ladder; 'balanced' is a
        # no-op, so a profile-less call is unchanged.
        shifted = shift_priority(req.priority, model_spend_profile)
        if shifted != req.priority:
            req = req.model_copy(update={"priority": shifted})
        resolved.append((idx, req))
    # Assign the most-demanding roles first so the strongest models go to the
    # work that needs them, never wasted on a low-demand role.
    resolved.sort(key=lambda pair: demand_tier(pair[1]), reverse=True)

    results = [
        match
        for idx, req in resolved
        if (match := _match_agent(idx, req, ctx)) is not None
    ]
    results.sort(key=lambda match: match.agent_index)
    return results


# Reported match score for a demand-tier assignment: a deliberate tier pick,
# not a fuzzy capability match, so it carries full confidence.
_TIERED_MATCH_SCORE: Final[float] = 1.0


@dataclass(slots=True)
class _MatchContext:
    """Shared batch-matching state: the unified pool + running family spread.

    A dataclass (not a ``NamedTuple``) makes the mutability honest:
    ``family_usage`` is a live ``Counter`` that ``_match_agent`` increments
    after each assignment so later agents draw the least-used family.
    """

    pool: tuple[ProviderModelConfig, ...]
    owner: dict[int, str]
    local_ids: frozenset[int]
    family_usage: Counter[str]
    config: ModelMatcherConfig
    strategy: ModelSelectionStrategy


def _build_pool(
    providers: Mapping[str, _ProviderWithModels],
) -> tuple[tuple[ProviderModelConfig, ...], dict[int, str], frozenset[int]]:
    """Flatten every provider's models into one pool + id maps.

    Returns:
        ``(pool, owner, local_ids)`` where ``owner`` maps ``id(model)`` to its
        provider name and ``local_ids`` holds the ``id(model)`` of every model
        served by a locally-hosted provider (free to run).
    """
    pool: list[ProviderModelConfig] = []
    owner: dict[int, str] = {}
    local_ids: set[int] = set()
    for pname, pcfg in providers.items():
        # An agent-ineligible provider (e.g. a gateway kept for feature calls
        # only) contributes no models to the seeding pool, so no agent is ever
        # assigned one of its models at provisioning.
        if not pcfg.agent_eligible:
            continue
        provider_is_local = is_local_url(pcfg.base_url)
        for model in pcfg.models:
            pool.append(model)
            owner[id(model)] = pname
            if provider_is_local:
                local_ids.add(id(model))
    return tuple(pool), owner, frozenset(local_ids)


def _match_agent(
    idx: int,
    req: ModelRequirement,
    ctx: _MatchContext,
) -> ModelMatch | None:
    """Assign one agent a model across the unified provider pool.

    An explicit id / family / pattern reference is honoured via the strategy
    (pin the newest match). Otherwise the role's declared capability demand
    selects a cost tier, and the agent draws the least-used family at (or
    nearest) that tier, so the model matches the work's difficulty and the
    roster fans out across model lines.

    Returns:
        The ``ModelMatch``, or ``None`` when nothing clears the hard filters.
    """
    if req.model_id or req.family or req.model_pattern:
        model, score = ctx.strategy.select(req, ctx.pool, ctx.config)
    else:
        eligible = [m for m in ctx.pool if passes_hard_filters(m, req)]
        eligible = above_usable_floor(eligible, ctx.config.min_usable_parameters)
        eligible = enforce_cloud_floor(
            eligible,
            ctx.local_ids,
            ctx.config.min_cloud_tier,
            ctx.config.tier_overrides,
        )
        # The locality preference is opt-out: pass an empty set to fall back to
        # pure capability + family-spread selection.
        prefer = ctx.local_ids if ctx.config.prefer_local else frozenset()
        model = select_for_demand(
            eligible,
            demand_tier(req),
            ctx.family_usage,
            ctx.config.tier_overrides,
            local_ids=prefer,
        )
        score = _TIERED_MATCH_SCORE if model is not None else 0.0

    provider = ctx.owner.get(id(model)) if model is not None else None
    if model is None or provider is None:
        # WARNING, not DEBUG: an agent with no model does no work at all, and
        # the tool-calling floor applies to every agent, so one misconfigured
        # provider catalogue can starve a whole roster at once. That has to be
        # visible at a level operators actually collect.
        logger.warning(
            TEMPLATE_MODEL_MATCH_FAILED,
            agent_index=idx,
            reason="no_compliant_model",
        )
        return None

    ctx.family_usage[model.metadata.family or model.id] += 1
    logger.debug(
        TEMPLATE_MODEL_MATCH_SUCCESS,
        agent_index=idx,
        provider=provider,
        model=model.id,
        score=score,
    )
    return ModelMatch(
        agent_index=idx,
        provider_name=provider,
        model_id=model.id,
        capability=derive_capability(model, ctx.config),
        score=score,
    )
