# module-kind: code
"""Model-id to tier-archetype resolution for the cost-dial.

The Pareto downgrade traversal and the stub benchmark provider both need
to map a model id onto one of the canonical tier archetypes
(``large`` / ``medium`` / ``small`` / ``local-small``). The default
heuristic recognises the vendor-agnostic ``example-<tier>-<rev>`` /
``...-local-small-...`` ids; an operator running arbitrary model ids
supplies a :class:`ModelTierMap` of explicit overrides so those ids also
resolve a tier (additive: the heuristic still applies to anything the
map does not name).
"""

import re
from collections.abc import Mapping
from typing import Final, Literal, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

#: A canonical quality-tier label. The single source of truth for the
#: tier vocabulary: ``TIERS`` derives from it and config boundaries type
#: their override values against it so a non-canonical tier is rejected
#: at construction rather than at wiring.
TierName = Literal["large", "medium", "small", "local-small"]

#: The canonical quality tiers the cost-dial reasons about.
TIERS: Final[frozenset[str]] = frozenset(get_args(TierName))

#: A documented ``example-<tier>-<rev>`` archetype id. Anchored on the
#: ``example-`` prefix so only the vendor-agnostic sample ids resolve a
#: tier; arbitrary operator ids fall through to ``None`` (and an explicit
#: :class:`ModelTierMap` override) rather than being silently classified.
_EXAMPLE_TIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^example-(large|medium|small)(?:-.+)?$"
)
#: A contiguous ``local-small`` segment anywhere in the id. Contiguity
#: matters: a non-adjacent ``...local...small...`` id is unrelated and
#: must not classify as the ``local-small`` archetype.
_LOCAL_SMALL_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|-)local-small(?:-|$)")


def heuristic_tier(model_id: str) -> str | None:
    """Map a vendor-agnostic ``example-<tier>-<rev>`` id to its tier.

    Matching is restricted to the documented archetype shapes -- the
    ``example-<tier>-<rev>`` sample ids and a contiguous ``local-small``
    segment -- so an unrelated id (e.g. ``foo-local-bar-small-baz``) is
    not silently classified; such ids resolve a tier only through an
    explicit :class:`ModelTierMap` override.

    Returns:
        The tier label (``large`` / ``medium`` / ``small`` /
        ``local-small``), or ``None`` when the id matches no known
        archetype.
    """
    lowered = model_id.lower()
    if _LOCAL_SMALL_RE.search(lowered):
        return "local-small"
    match = _EXAMPLE_TIER_RE.match(lowered)
    if match is not None:
        return match.group(1)
    return None


class ModelTierMap(BaseModel):
    """Operator-configured ``model_id`` to tier-archetype overrides.

    An empty map (the default) leaves resolution entirely to
    :func:`heuristic_tier`, so a normal boot is unchanged.

    Attributes:
        overrides: Explicit ``model_id`` to tier mappings consulted
            before the heuristic. Each value must be a canonical tier.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    overrides: Mapping[NotBlankStr, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _overrides_are_canonical_tiers(self) -> Self:
        """Every override target must be a canonical tier.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an override maps to a non-canonical tier.
        """
        invalid = {
            model_id: tier
            for model_id, tier in self.overrides.items()
            if tier not in TIERS
        }
        if invalid:
            msg = (
                f"ModelTierMap overrides must target a canonical tier "
                f"{sorted(TIERS)}; got {invalid!r}"
            )
            raise ValueError(msg)
        return self


def resolve_tier(
    model_id: str,
    tier_map: ModelTierMap | None = None,
) -> str | None:
    """Resolve a model id to a tier, overrides first then the heuristic.

    Returns:
        The tier label, or ``None`` when neither the override map nor
        the heuristic recognises the id.
    """
    if tier_map is not None:
        override = tier_map.overrides.get(NotBlankStr(model_id))
        if override is not None:
            return override
    return heuristic_tier(model_id)


__all__ = ["TIERS", "ModelTierMap", "TierName", "heuristic_tier", "resolve_tier"]
