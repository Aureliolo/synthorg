# module-kind: code
"""Priority-axis scoring for the model matcher.

Pool-normalised value functions for the cost / quality / speed / balanced
optimisation axes, plus the composite model-strength signal (parameter
count blended with generation) that ranks the quality axis -- kept out of
``model_matcher`` so the engine module stays within its size budget.
"""

from collections.abc import Callable, Sequence
from typing import Final

from synthorg.config.schema import ProviderModelConfig

# Latency stand-in (ms) for models without a measured latency, so they
# sort last on the speed axis without using inf (frozen models forbid it).
_LATENCY_UNKNOWN_MS: Final[int] = 10_000_000

# Weight of the (pool-normalised) strength axis in the balanced blend; the
# remainder weights cheapness. 0.5 splits quality and cost evenly.
_BALANCED_STRENGTH_WEIGHT: Final[float] = 0.5

# Weight of parameter-count vs generation within the composite strength
# signal. Parameter count is the dominant "miles better" size proxy; the
# remainder lets a newer model edge out an older one of the same size.
_STRENGTH_PARAM_WEIGHT: Final[float] = 0.7

# Cost<->quality ladder the company model-tier profile shifts an agent's
# priority along: 'economy' nudges one rung cheaper, 'premium' one rung
# stronger, 'balanced' leaves it untouched. The 'speed' axis is orthogonal
# (latency, not cost/quality) and is never shifted, so a fast role stays fast
# regardless of the profile.
_PRIORITY_LADDER: Final[tuple[str, ...]] = ("cost", "balanced", "quality")


def shift_priority(priority: str, model_spend_profile: str) -> str:
    """Shift a priority along the cost<->quality ladder per the tier profile.

    Preserves relative ordering across the roster (a quality role stays
    stronger than a cost role) while biasing the whole company cheaper or
    stronger; the company default 'balanced' is a no-op.

    Args:
        priority: The agent's resolved optimisation axis.
        model_spend_profile: One of 'economy', 'balanced', 'premium'.

    Returns:
        The shifted priority, unchanged for 'balanced', the 'speed' axis, or
        any unrecognised priority value.
    """
    if model_spend_profile == "premium":
        step = 1
    elif model_spend_profile == "economy":
        step = -1
    else:
        return priority
    if priority not in _PRIORITY_LADDER:
        return priority
    raw_idx = _PRIORITY_LADDER.index(priority) + step
    new_idx = min(max(raw_idx, 0), len(_PRIORITY_LADDER) - 1)
    return _PRIORITY_LADDER[new_idx]


def _model_generation(model: ProviderModelConfig) -> float:
    """Return the model's generation, or ``0.0`` when unknown.

    Returns:
        The parsed ``metadata.generation`` or ``0.0``.
    """
    return model.metadata.generation if model.metadata.generation is not None else 0.0


def _model_parameter_count(model: ProviderModelConfig) -> float:
    """Return the model's parameter count, or ``0.0`` when unknown.

    Returns:
        The ``metadata.parameter_count`` as a float, or ``0.0``.
    """
    count = model.metadata.parameter_count
    return float(count) if count is not None else 0.0


def _normalized_strength(
    pool: Sequence[ProviderModelConfig],
) -> Callable[[ProviderModelConfig], float]:
    """Build a pool-normalised ``[0, 1]`` model-strength function.

    Strength blends parameter count (the dominant size/"miles better" proxy)
    with generation/recency. Each axis is normalised within *pool*, and an
    axis with no signal across the pool drops out, so a frontier 756B cloud
    model outranks a small local one without an absent axis dragging anyone
    to zero.

    Returns:
        A callable mapping a model to its ``[0, 1]`` strength.
    """
    params = [_model_parameter_count(m) for m in pool]
    gens = [_model_generation(m) for m in pool]
    p_min, p_max = min(params), max(params)
    g_min, g_max = min(gens), max(gens)
    p_span, g_span = (p_max - p_min) or 1.0, (g_max - g_min) or 1.0
    has_params, has_gens = p_max > 0.0, g_max > 0.0

    def strength(model: ProviderModelConfig) -> float:
        norm_p = (_model_parameter_count(model) - p_min) / p_span
        norm_g = (_model_generation(model) - g_min) / g_span
        if has_params and has_gens:
            return (
                _STRENGTH_PARAM_WEIGHT * norm_p
                + (1.0 - _STRENGTH_PARAM_WEIGHT) * norm_g
            )
        if has_params:
            return norm_p
        return norm_g if has_gens else 0.0

    return strength


def priority_ranker(
    pool: Sequence[ProviderModelConfig],
    priority: str,
) -> Callable[[ProviderModelConfig], float]:
    """Build a higher-is-better value function for *priority* over *pool*.

    ``quality`` ranks on pool-normalised model strength (parameter count +
    generation). ``balanced`` blends that strength with cheapness, each
    normalised within *pool* so the incomparable scales contribute evenly.
    ``cost`` / ``speed`` use a single per-model axis.

    Returns:
        A callable mapping a model to its priority-axis value.
    """
    if priority == "quality":
        return _normalized_strength(pool)
    if priority != "balanced":
        return lambda m: _priority_value(m, priority)

    strength = _normalized_strength(pool)
    costs = [m.cost_per_1k_input for m in pool]
    cost_min, cost_span = min(costs), (max(costs) - min(costs)) or 1.0

    def balanced(model: ProviderModelConfig) -> float:
        norm_cost = (model.cost_per_1k_input - cost_min) / cost_span
        return _BALANCED_STRENGTH_WEIGHT * strength(model) + (
            1.0 - _BALANCED_STRENGTH_WEIGHT
        ) * (1.0 - norm_cost)

    return balanced


def _priority_value(model: ProviderModelConfig, priority: str) -> float:
    """Higher-is-better value of *model* on a single (non-balanced) axis.

    Returns:
        Negative cost for cost, negative latency for speed, and generation
        as the default.
    """
    if priority == "cost":
        return -model.cost_per_1k_input
    if priority == "speed":
        latency = model.estimated_latency_ms
        return -float(latency if latency is not None else _LATENCY_UNKNOWN_MS)
    return _model_generation(model)
