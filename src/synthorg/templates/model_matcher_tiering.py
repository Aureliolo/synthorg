# module-kind: code
"""Seniority-tiered model selection with family-aware spread.

Replaces winner-take-all selection (every quality agent getting the single
biggest model) with a level-driven assignment: rank the eligible models by a
coarse quality signal, map the agent's seniority to a band of that ranking,
and pick within the band -- preferring the least-used family so agents spread
across the available model lines instead of all landing on one.
"""

import re
from collections import Counter
from collections.abc import Sequence
from typing import Final

from synthorg.config.schema import ProviderModelConfig

# Seniority levels low-to-high; the matcher bands and the role-vs-level
# reconciliation both rank against this order.
_SENIORITY_ORDER: Final[tuple[str, ...]] = (
    "junior",
    "mid",
    "senior",
    "principal",
    "lead",
    "director",
    "vp",
    "c_suite",
)

# Role title -> implied seniority, highest first. A template can leave a CEO at
# ``level: mid``; the role still puts them in the executive band so leadership
# is never assigned a smaller model than its own reports.
_ROLE_LEVEL_RULES: Final[tuple[tuple[str, str], ...]] = (
    (r"\bceo\b|chief executive|founder|president", "c_suite"),
    (r"^chief|\bc[a-z]o\b", "c_suite"),
    (r"vice president|\bvp\b", "vp"),
    (r"director|head of", "director"),
    (r"\blead\b|principal", "lead"),
)


def _role_level(role: str) -> str | None:
    """Return the seniority a role title implies, or ``None``."""
    lowered = role.lower()
    for pattern, level in _ROLE_LEVEL_RULES:
        if re.search(pattern, lowered):
            return level
    return None


def effective_level(level: str | None, role: str | None) -> str | None:
    """Reconcile an agent's ``level`` field with the seniority its role implies.

    Returns the more-senior of the two, so a CEO stamped ``level: mid`` still
    bands as executive.

    Returns:
        The higher-ranked level string, or ``level`` when neither is known.
    """
    role_level = _role_level(role or "")
    candidates = [lv for lv in (level, role_level) if lv in _SENIORITY_ORDER]
    if not candidates:
        return level
    return max(candidates, key=_SENIORITY_ORDER.index)


# Per-level band as ``(low, high)`` fractions of the quality-desc ranked list:
# c-suite/exec draw from the strongest models, juniors from the smallest, with
# overlap so adjacent levels can share when the catalogue is thin.
_LEVEL_BANDS: Final[dict[str, tuple[float, float]]] = {
    "c_suite": (0.0, 0.2),
    "vp": (0.0, 0.25),
    "director": (0.1, 0.35),
    "lead": (0.1, 0.4),
    "principal": (0.15, 0.45),
    "senior": (0.25, 0.55),
    "mid": (0.45, 0.8),
    "junior": (0.7, 1.0),
}
# Unknown level lands mid-catalogue rather than at either extreme.
_DEFAULT_BAND: Final[tuple[float, float]] = (0.45, 0.8)


def _quality_key(model: ProviderModelConfig) -> tuple[float, float, int]:
    """Sort key ranking a model by coarse strength (higher is stronger).

    Returns:
        ``(parameter_count, generation, max_context)`` with missing values
        treated as zero, so a larger / newer / longer-context model sorts
        ahead.
    """
    meta = model.metadata
    return (
        float(meta.parameter_count) if meta.parameter_count is not None else 0.0,
        meta.generation if meta.generation is not None else 0.0,
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


def _level_band(level: str | None, count: int) -> tuple[int, int]:
    """Resolve a seniority level to ``[lo, hi)`` indices in a ranked list.

    Returns:
        Half-open index bounds into a ``count``-long quality-desc ranking.
    """
    lo_frac, hi_frac = _LEVEL_BANDS.get(level or "", _DEFAULT_BAND)
    lo = int(lo_frac * count)
    hi = max(lo + 1, int(hi_frac * count))
    return lo, min(hi, count)


def select_tiered(
    eligible_ranked: Sequence[ProviderModelConfig],
    level: str | None,
    family_usage: Counter[str],
) -> ProviderModelConfig | None:
    """Pick a model for an agent's seniority band, spreading across families.

    *eligible_ranked* must already be quality-desc ordered and hard-filtered
    for the agent's requirement. The agent's level selects a band; within it
    the least-used family wins (ties resolve to the stronger model, since the
    band is quality-ordered), so a roster spreads across model lines instead
    of stacking on one.

    Args:
        eligible_ranked: Hard-filter-passing models, strongest first.
        level: The agent's seniority level (``None`` -> mid band).
        family_usage: Running per-family assignment counts, updated by caller.

    Returns:
        The chosen model, or ``None`` when nothing is eligible.
    """
    if not eligible_ranked:
        return None
    lo, hi = _level_band(level, len(eligible_ranked))
    band = eligible_ranked[lo:hi] or eligible_ranked
    best_index = min(
        range(len(band)),
        key=lambda i: (family_usage[band[i].metadata.family or band[i].id], i),
    )
    return band[best_index]
