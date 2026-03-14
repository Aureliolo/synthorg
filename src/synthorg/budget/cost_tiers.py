"""Cost tier definitions and classification.

Provides configurable metadata for cost tiers: price ranges, display
properties, and model-to-tier classification.  The built-in ``CostTier``
enum (``synthorg.core.enums``) remains for backward compatibility;
this module adds a configurable layer on top.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_TIER_CLASSIFY_MISS,
    BUDGET_TIER_RESOLVED,
)

logger = get_logger(__name__)


class CostTierDefinition(BaseModel):
    """Metadata for a single cost tier.

    Attributes:
        id: Unique tier identifier (e.g. ``"low"``, ``"custom-budget"``).
        display_name: Human-readable name.
        description: What this tier represents.
        price_range_min: Minimum cost_per_1k_total for models in this
            tier (USD).
        price_range_max: Maximum cost_per_1k_total; ``None`` means
            unbounded above.
        color: Hex color for UI rendering.
        icon: Icon identifier for UI rendering.
        sort_order: Display ordering (lower = cheaper).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(description="Unique tier identifier")
    display_name: NotBlankStr = Field(description="Human-readable name")
    description: str = Field(
        default="",
        description="What this tier represents",
    )
    price_range_min: float = Field(
        default=0.0,
        ge=0.0,
        description="Minimum cost_per_1k_total (USD)",
    )
    price_range_max: float | None = Field(
        default=None,
        ge=0.0,
        description="Maximum cost_per_1k_total (USD); None = unbounded",
    )
    color: str = Field(
        default="#6b7280",
        description="Hex color for UI",
    )
    icon: str = Field(
        default="circle",
        description="Icon identifier for UI",
    )
    sort_order: int = Field(
        default=0,
        description="Display ordering (lower = cheaper)",
    )

    @model_validator(mode="after")
    def _validate_price_range(self) -> Self:
        """Ensure max > min when both are set.

        A zero-width range (min == max) with a finite max can never
        match any cost because classification uses ``[min, max)``
        semantics.
        """
        if self.price_range_max is not None:
            if self.price_range_max < self.price_range_min:
                msg = (
                    f"price_range_max ({self.price_range_max}) must be "
                    f"> price_range_min ({self.price_range_min})"
                )
                raise ValueError(msg)
            if self.price_range_max == self.price_range_min:
                msg = (
                    f"price_range_max ({self.price_range_max}) must be "
                    f"> price_range_min ({self.price_range_min}); "
                    f"zero-width range can never match with [min, max) "
                    f"semantics"
                )
                raise ValueError(msg)
        return self


class CostTiersConfig(BaseModel):
    """Configuration for cost tier definitions.

    Attributes:
        tiers: User-defined tier overrides/additions.
        include_builtin: Whether to merge built-in default tiers.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    tiers: tuple[CostTierDefinition, ...] = Field(
        default=(),
        description="User-defined tier overrides/additions",
    )
    include_builtin: bool = Field(
        default=True,
        description="Whether to merge built-in default tiers",
    )

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> Self:
        """Ensure tier IDs are unique within user-defined tiers."""
        seen: set[str] = set()
        dupes: set[str] = set()
        for t in self.tiers:
            if t.id in seen:
                dupes.add(t.id)
            seen.add(t.id)
        if dupes:
            msg = f"Duplicate tier IDs: {sorted(dupes)}"
            raise ValueError(msg)
        return self


BUILTIN_TIERS: tuple[CostTierDefinition, ...] = (
    CostTierDefinition(
        id="low",
        display_name="Low",
        description="Budget-friendly models for simple tasks",
        price_range_min=0.0,
        price_range_max=0.002,
        color="#22c55e",
        icon="circle",
        sort_order=0,
    ),
    CostTierDefinition(
        id="medium",
        display_name="Medium",
        description="Balanced cost-performance models",
        price_range_min=0.002,
        price_range_max=0.01,
        color="#eab308",
        icon="circle",
        sort_order=1,
    ),
    CostTierDefinition(
        id="high",
        display_name="High",
        description="High-capability models for complex tasks",
        price_range_min=0.01,
        price_range_max=0.03,
        color="#f97316",
        icon="circle",
        sort_order=2,
    ),
    CostTierDefinition(
        id="premium",
        display_name="Premium",
        description="Top-tier models for maximum capability",
        price_range_min=0.03,
        price_range_max=None,
        color="#ef4444",
        icon="circle",
        sort_order=3,
    ),
)


def resolve_tiers(
    config: CostTiersConfig,
) -> tuple[CostTierDefinition, ...]:
    """Merge built-in and user-defined tiers, sorted by sort_order.

    User tiers override built-in tiers with the same ID.

    Args:
        config: Cost tiers configuration.

    Returns:
        Merged and sorted tuple of tier definitions.
    """
    if not config.include_builtin:
        result = sorted(config.tiers, key=lambda t: t.sort_order)
        logger.debug(
            BUDGET_TIER_RESOLVED,
            tier_count=len(result),
            include_builtin=False,
        )
        return tuple(result)

    # User tiers override built-in by ID
    user_ids = {t.id for t in config.tiers}
    merged: list[CostTierDefinition] = [
        t for t in BUILTIN_TIERS if t.id not in user_ids
    ]
    merged.extend(config.tiers)
    merged.sort(key=lambda t: t.sort_order)

    logger.debug(
        BUDGET_TIER_RESOLVED,
        tier_count=len(merged),
        include_builtin=True,
        overridden_ids=sorted(user_ids & {t.id for t in BUILTIN_TIERS}),
    )
    return tuple(merged)


def classify_model_tier(
    cost_per_1k_total: float,
    tiers: tuple[CostTierDefinition, ...],
) -> str | None:
    """Classify a model into a cost tier based on total cost per 1k tokens.

    Matches the first tier whose price range contains the given cost.
    Range check: ``min <= cost < max`` (or ``min <= cost`` if max is
    ``None``).  If tiers have overlapping ranges, the first match in
    iteration order wins — callers should ensure tiers are sorted by
    ``sort_order``.

    Args:
        cost_per_1k_total: Combined ``cost_per_1k_input +
            cost_per_1k_output``.
        tiers: Resolved tier definitions (should be sorted by
            sort_order).

    Returns:
        Tier ID of the matching tier, or ``None`` if no tier matches.
    """
    if cost_per_1k_total < 0:
        logger.warning(
            BUDGET_TIER_CLASSIFY_MISS,
            cost_per_1k_total=cost_per_1k_total,
            tier_count=len(tiers),
            reason="negative_cost",
        )
        return None

    for tier in tiers:
        if tier.price_range_max is None:
            if cost_per_1k_total >= tier.price_range_min:
                return tier.id
        elif tier.price_range_min <= cost_per_1k_total < tier.price_range_max:
            return tier.id

    logger.debug(
        BUDGET_TIER_CLASSIFY_MISS,
        cost_per_1k_total=cost_per_1k_total,
        tier_count=len(tiers),
    )
    return None
