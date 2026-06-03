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

from collections.abc import Mapping
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

#: The canonical quality tiers the cost-dial reasons about.
TIERS: Final[frozenset[str]] = frozenset({"large", "medium", "small", "local-small"})


def heuristic_tier(model_id: str) -> str | None:
    """Map a vendor-agnostic ``example-<tier>-<rev>`` id to its tier.

    Returns:
        The tier label (``large`` / ``medium`` / ``small`` /
        ``local-small``), or ``None`` when the id matches no known
        archetype.
    """
    parts = model_id.split("-")
    if len(parts) < 2:  # noqa: PLR2004 -- a tier id needs at least <tier>-<rev>
        return None
    if "local" in parts and "small" in parts:
        return "local-small"
    candidate = parts[-2].lower()
    if candidate in {"large", "medium", "small"}:
        return candidate
    return None


class ModelTierMap(BaseModel):
    """Operator-configured ``model_id`` to tier-archetype overrides.

    An empty map (the default) leaves resolution entirely to
    :func:`heuristic_tier`, so a normal boot is unchanged.

    Attributes:
        overrides: Explicit ``model_id`` to tier mappings consulted
            before the heuristic. Each value must be a canonical tier.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

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


__all__ = ["TIERS", "ModelTierMap", "heuristic_tier", "resolve_tier"]
