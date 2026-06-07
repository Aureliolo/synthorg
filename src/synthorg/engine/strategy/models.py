# module-kind: adapter
"""Core models for the strategy module.

Config models (frozen Pydantic) for strategy configuration and domain
models for runtime strategic analysis.  All enums that are specific
to the strategy domain are defined here;
:class:`~synthorg.hr.strategy_mode.StrategicOutputMode` is imported from
the HR package because :class:`~synthorg.core.agent.AgentIdentity`
references it.
"""

import copy
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.hr.strategy_mode import StrategicOutputMode
from synthorg.observability import get_logger
from synthorg.observability.events.strategy import STRATEGY_CONFIG_VALIDATED

logger = get_logger(__name__)

# ── Strategy-specific enums ────────────────────────────────────


class CostTierPreset(StrEnum):
    """Cost tier for strategic analysis depth."""

    MINIMAL = "minimal"
    MODERATE = "moderate"
    GENEROUS = "generous"


class ConfidenceFormat(StrEnum):
    """Output format for confidence calibration."""

    STRUCTURED = "structured"
    NARRATIVE = "narrative"
    BOTH = "both"
    PROBABILITY = "probability"


class ContextSource(StrEnum):
    """Source for strategic context data."""

    CONFIG = "config"
    MEMORY = "memory"
    COMPOSITE = "composite"


class ConsensusAction(StrEnum):
    """Action to take when consensus velocity is too high."""

    DEVIL_ADVOCATE = "devil_advocate"
    SLOW_DOWN = "slow_down"
    ESCALATE = "escalate"


class ConflictDetectionStrategy(StrEnum):
    """Strategy for detecting strategic conflicts."""

    AUTO = "auto"
    MANUAL = "manual"
    DISABLED = "disabled"


class PremortemParticipation(StrEnum):
    """Who participates in premortem analysis."""

    ALL = "all"
    STRATEGIC = "strategic"
    NONE = "none"


class ImpactDimension(StrEnum):
    """Dimensions for impact scoring."""

    BUDGET_IMPACT = "budget_impact"
    AUTHORITY_LEVEL = "authority_level"
    DECISION_TYPE = "decision_type"
    REVERSIBILITY = "reversibility"
    BLAST_RADIUS = "blast_radius"
    TIME_HORIZON = "time_horizon"
    STRATEGIC_ALIGNMENT = "strategic_alignment"


class PrincipleSeverity(StrEnum):
    """Severity level for constitutional principles."""

    INFORMATIONAL = "informational"
    WARNING = "warning"
    CRITICAL = "critical"


class Reversibility(StrEnum):
    """How reversible a decision is."""

    EASILY_REVERSIBLE = "easily_reversible"
    MODERATE = "moderate"
    LOCKED_IN = "locked_in"


class BlastRadius(StrEnum):
    """Scope of impact for a decision."""

    INDIVIDUAL = "individual"
    TEAM = "team"
    DEPARTMENT = "department"
    COMPANY_WIDE = "company_wide"


class TimeHorizon(StrEnum):
    """Time horizon for a decision's effects."""

    IMMEDIATE = "immediate"
    SHORT_TERM = "short_term"
    MEDIUM_TERM = "medium_term"
    LONG_TERM = "long_term"


# ── Config sub-models ──────────────────────────────────────────


class ConfidenceConfig(BaseModel):
    """Confidence calibration output configuration.

    Attributes:
        format: Output format for confidence metadata.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    format: ConfidenceFormat = Field(
        default=ConfidenceFormat.STRUCTURED,
        description="Output format for confidence metadata",
    )


class ConsensusVelocityConfig(BaseModel):
    """Configuration for consensus velocity detection.

    When agents reach consensus too quickly (above threshold),
    the configured action is triggered to slow down groupthink.

    Attributes:
        action: Action to take when consensus is too fast.
        threshold: Consensus velocity threshold (0.0-1.0).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: ConsensusAction = Field(
        default=ConsensusAction.DEVIL_ADVOCATE,
        description="Action when consensus velocity exceeds threshold",
    )
    threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Consensus velocity threshold (0.0-1.0)",
    )


class PremortemConfig(BaseModel):
    """Premortem analysis configuration.

    Attributes:
        participants: Who participates in premortem analysis.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    participants: PremortemParticipation = Field(
        default=PremortemParticipation.ALL,
        description="Who participates in premortem analysis",
    )


class ConflictDetectionConfig(BaseModel):
    """Strategic conflict detection configuration.

    Attributes:
        strategy: Detection strategy to use.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: ConflictDetectionStrategy = Field(
        default=ConflictDetectionStrategy.AUTO,
        description="Conflict detection strategy",
    )


class StrategicContextConfig(BaseModel):
    """Static strategic context from configuration.

    Provides company-level context that shapes how strategic lenses
    and constitutional principles are applied.

    Attributes:
        source: Where to read context from.
        maturity_stage: Company maturity stage.
        industry: Industry sector.
        competitive_position: Market position.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source: ContextSource = Field(
        default=ContextSource.CONFIG,
        description="Context data source",
    )
    maturity_stage: NotBlankStr = Field(
        default="growth",
        description="Company maturity stage",
    )
    industry: NotBlankStr = Field(
        default="technology",
        description="Industry sector",
    )
    competitive_position: NotBlankStr = Field(
        default="challenger",
        description="Market competitive position",
    )


_WEIGHT_SUM_TOLERANCE: float = 1e-6


class ProgressiveWeights(BaseModel):
    """Weights for 7-dimension impact scoring.

    All weights must sum to 1.0 (within floating-point tolerance).

    Attributes:
        budget_impact: Weight for budget impact dimension.
        authority_level: Weight for authority level dimension.
        decision_type: Weight for decision type dimension.
        reversibility: Weight for reversibility dimension.
        blast_radius: Weight for blast radius dimension.
        time_horizon: Weight for time horizon dimension.
        strategic_alignment: Weight for strategic alignment dimension.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    budget_impact: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Weight for budget impact dimension",
    )
    authority_level: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Weight for authority level dimension",
    )
    decision_type: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Weight for decision type dimension",
    )
    reversibility: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Weight for reversibility dimension",
    )
    blast_radius: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Weight for blast radius dimension",
    )
    time_horizon: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Weight for time horizon dimension",
    )
    strategic_alignment: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Weight for strategic alignment dimension",
    )

    @model_validator(mode="after")
    def _validate_weights_sum(self) -> Self:
        """Ensure all weights sum to 1.0 within tolerance.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If the seven weight fields do not sum to
                1.0 within ``_WEIGHT_SUM_TOLERANCE``.
        """
        total = (
            self.budget_impact
            + self.authority_level
            + self.decision_type
            + self.reversibility
            + self.blast_radius
            + self.time_horizon
            + self.strategic_alignment
        )
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            msg = f"Progressive weights must sum to 1.0, got {total:.10f}"
            logger.warning(
                STRATEGY_CONFIG_VALIDATED,
                model="ProgressiveWeights",
                error=msg,
                total=total,
            )
            raise ValueError(msg)
        return self

    def as_dict(self) -> dict[ImpactDimension, float]:
        """Return weights as a dimension-keyed dict.

        Returns:
            A dict mapping each :class:`ImpactDimension` to its weight.
        """
        return {
            ImpactDimension.BUDGET_IMPACT: self.budget_impact,
            ImpactDimension.AUTHORITY_LEVEL: self.authority_level,
            ImpactDimension.DECISION_TYPE: self.decision_type,
            ImpactDimension.REVERSIBILITY: self.reversibility,
            ImpactDimension.BLAST_RADIUS: self.blast_radius,
            ImpactDimension.TIME_HORIZON: self.time_horizon,
            ImpactDimension.STRATEGIC_ALIGNMENT: self.strategic_alignment,
        }


class ProgressiveThresholds(BaseModel):
    """Thresholds for progressive cost tier resolution.

    Impact scores below ``moderate`` map to :attr:`CostTierPreset.MINIMAL`,
    scores between ``moderate`` and ``generous`` map to
    :attr:`CostTierPreset.MODERATE`, and scores at or above ``generous``
    map to :attr:`CostTierPreset.GENEROUS`.

    Attributes:
        moderate: Lower threshold for moderate tier.
        generous: Lower threshold for generous tier.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    moderate: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Lower threshold for moderate tier",
    )
    generous: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Lower threshold for generous tier",
    )

    @model_validator(mode="after")
    def _validate_ordering(self) -> Self:
        """Ensure moderate < generous.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If ``moderate >= generous``.
        """
        if self.moderate >= self.generous:
            msg = (
                f"moderate threshold ({self.moderate}) must be "
                f"less than generous threshold ({self.generous})"
            )
            logger.warning(
                STRATEGY_CONFIG_VALIDATED,
                model="ProgressiveThresholds",
                error=msg,
            )
            raise ValueError(msg)
        return self


class ProgressiveConfig(BaseModel):
    """Progressive cost tier resolution configuration.

    Attributes:
        weights: Dimension weights for impact scoring.
        thresholds: Thresholds for cost tier resolution.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    weights: ProgressiveWeights = Field(
        default_factory=ProgressiveWeights,
        description="Dimension weights for impact scoring",
    )
    thresholds: ProgressiveThresholds = Field(
        default_factory=ProgressiveThresholds,
        description="Thresholds for cost tier resolution",
    )


class ConstitutionalPrincipleConfig(BaseModel):
    """Configuration for constitutional principle packs.

    Attributes:
        pack: Name of the built-in or user principle pack to load.
        custom: Additional custom principles appended after the pack.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    pack: NotBlankStr = Field(
        default="default",
        description="Principle pack name to load",
    )
    custom: tuple[dict[str, Any], ...] = Field(
        default=(),
        description="Custom principles appended to the pack",
    )

    @model_validator(mode="after")
    def _deepcopy_custom(self) -> Self:
        """Defensive copy so callers cannot mutate frozen model.

        Returns:
            ``self`` with its ``custom`` tuple replaced by a
            ``deepcopy`` so external aliases do not survive.
        """
        if self.custom:
            object.__setattr__(
                self,
                "custom",
                copy.deepcopy(self.custom),
            )
        return self


# ── Top-level strategy config ──────────────────────────────────


class StrategyConfig(BaseModel):
    """Top-level strategy and trendslop mitigation configuration.

    Aggregates all strategy sub-configurations into a single frozen
    model.  Added to :class:`~synthorg.config.schema.RootConfig` as
    the ``strategy`` field.

    Attributes:
        output_mode: Default strategic output mode for agents.
        cost_tier: Default cost tier preset.
        default_lenses: Strategic lenses to apply by default.
        constitutional_principles: Principle pack configuration.
        confidence: Confidence calibration output configuration.
        consensus_velocity: Consensus velocity detection configuration.
        premortem: Premortem analysis configuration.
        conflict_detection: Strategic conflict detection configuration.
        context: Strategic context configuration.
        progressive: Progressive cost tier resolution configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    output_mode: StrategicOutputMode = Field(
        default=StrategicOutputMode.ADVISOR,
        description="Default strategic output mode",
    )
    cost_tier: CostTierPreset = Field(
        default=CostTierPreset.MODERATE,
        description="Default cost tier preset",
    )
    default_lenses: tuple[NotBlankStr, ...] = Field(
        default=("contrarian", "risk_focused", "cost_focused", "status_quo"),
        description="Strategic lenses to apply by default",
    )
    constitutional_principles: ConstitutionalPrincipleConfig = Field(
        default_factory=ConstitutionalPrincipleConfig,
        description="Principle pack configuration",
    )
    confidence: ConfidenceConfig = Field(
        default_factory=ConfidenceConfig,
        description="Confidence calibration configuration",
    )
    consensus_velocity: ConsensusVelocityConfig = Field(
        default_factory=ConsensusVelocityConfig,
        description="Consensus velocity detection configuration",
    )
    premortem: PremortemConfig = Field(
        default_factory=PremortemConfig,
        description="Premortem analysis configuration",
    )
    conflict_detection: ConflictDetectionConfig = Field(
        default_factory=ConflictDetectionConfig,
        description="Strategic conflict detection configuration",
    )
    context: StrategicContextConfig = Field(
        default_factory=StrategicContextConfig,
        description="Strategic context configuration",
    )
    progressive: ProgressiveConfig = Field(
        default_factory=ProgressiveConfig,
        description="Progressive cost tier resolution configuration",
    )

    @model_validator(mode="after")
    def _validate_lenses(self) -> Self:
        """Ensure at least one lens is configured and all names are valid.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If ``default_lenses`` is empty or any lens name
                does not match a registered :class:`StrategicLens`.
        """
        if not self.default_lenses:
            msg = "default_lenses must contain at least one lens"
            logger.warning(
                STRATEGY_CONFIG_VALIDATED,
                model="StrategyConfig",
                error=msg,
            )
            raise ValueError(msg)

        from synthorg.engine.strategy.lenses import StrategicLens  # noqa: PLC0415

        available = {lens.value for lens in StrategicLens}
        for lens_name in self.default_lenses:
            if lens_name.lower() not in available:
                msg = f"Unknown lens {lens_name!r}. Available: {sorted(available)}"
                logger.warning(
                    STRATEGY_CONFIG_VALIDATED,
                    model="StrategyConfig",
                    error=msg,
                )
                raise ValueError(msg)
        return self


# ── Domain models (runtime) ────────────────────────────────────


class StrategicContext(BaseModel):
    """Runtime snapshot of strategic context.

    Immutable representation of the company's strategic position
    used to shape lens application and principle evaluation.

    Attributes:
        maturity_stage: Company maturity stage.
        industry: Industry sector.
        competitive_position: Market competitive position.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    maturity_stage: NotBlankStr = Field(description="Company maturity stage")
    industry: NotBlankStr = Field(description="Industry sector")
    competitive_position: NotBlankStr = Field(
        description="Market competitive position",
    )


class ConstitutionalPrinciple(BaseModel):
    """A single constitutional principle for anti-trendslop mitigation.

    Attributes:
        id: Unique principle identifier within a pack.
        text: The principle rule text injected into prompts.
        category: Principle category for grouping.
        severity: How strictly this principle must be followed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique principle identifier")
    text: NotBlankStr = Field(description="Principle rule text")
    category: NotBlankStr = Field(
        default="anti_trendslop",
        description="Principle category",
    )
    severity: PrincipleSeverity = Field(
        default=PrincipleSeverity.WARNING,
        description="Enforcement severity level",
    )


class PrinciplePack(BaseModel):
    """A collection of constitutional principles.

    Attributes:
        name: Pack identifier.
        version: Semantic version string.
        description: Human-readable pack description.
        principles: Ordered tuple of principles in this pack.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Pack identifier")
    version: NotBlankStr = Field(description="Semantic version string")
    description: str = Field(default="", description="Pack description")
    principles: tuple[ConstitutionalPrinciple, ...] = Field(
        default=(),
        description="Principles in this pack",
    )

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> Self:
        """Ensure principle IDs are unique within the pack.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If any principle id appears more than once.
        """
        ids = [p.id for p in self.principles]
        if len(ids) != len(set(ids)):
            seen: set[str] = set()
            dupes: list[str] = []
            for pid in ids:
                if pid in seen:
                    dupes.append(pid)
                seen.add(pid)
            msg = f"Duplicate principle IDs in pack {self.name!r}: {sorted(set(dupes))}"
            raise ValueError(msg)
        return self


class RiskCard(BaseModel):
    """Risk assessment metadata for a strategic decision.

    Attributes:
        decision_type: Type of decision being made.
        reversibility: How easily the decision can be reversed.
        blast_radius: Scope of impact if the decision goes wrong.
        time_horizon: How far into the future effects extend.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    decision_type: NotBlankStr = Field(description="Type of decision")
    reversibility: Reversibility = Field(
        default=Reversibility.MODERATE,
        description="Decision reversibility",
    )
    blast_radius: BlastRadius = Field(
        default=BlastRadius.TEAM,
        description="Impact scope",
    )
    time_horizon: TimeHorizon = Field(
        default=TimeHorizon.MEDIUM_TERM,
        description="Effect time horizon",
    )


class ImpactScore(BaseModel):
    """Composite impact score across all dimensions.

    Attributes:
        dimensions: Per-dimension scores (0.0-1.0).
        composite: Weighted composite score (0.0-1.0).
        tier: Resolved cost tier based on composite score.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    dimensions: dict[str, float] = Field(
        description="Per-dimension scores (0.0-1.0)",
    )
    composite: float = Field(
        ge=0.0,
        le=1.0,
        description="Weighted composite score",
    )
    tier: CostTierPreset = Field(description="Resolved cost tier")

    @model_validator(mode="after")
    def _validate_dimension_values(self) -> Self:
        """Ensure all dimension scores are in [0.0, 1.0].

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If any dimension score is outside ``[0.0, 1.0]``.
        """
        for dim, score in self.dimensions.items():
            if not (0.0 <= score <= 1.0):
                msg = f"Dimension {dim!r} score {score} must be in [0.0, 1.0]"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _make_dimensions_readonly(self) -> Self:
        """Wrap dimensions in MappingProxyType for read-only enforcement.

        Returns:
            ``self`` with its ``dimensions`` field replaced by a
            ``MappingProxyType`` over a deepcopy so callers cannot
            mutate the shared dict.
        """
        object.__setattr__(
            self,
            "dimensions",
            MappingProxyType(copy.deepcopy(self.dimensions)),
        )
        return self


class ConfidenceMetadata(BaseModel):
    """Calibrated confidence information for a recommendation.

    Attributes:
        level: Point confidence estimate (0.0-1.0).
        range_lower: Lower bound of confidence range.
        range_upper: Upper bound of confidence range.
        assumptions: Key assumptions underlying the recommendation.
        uncertainty_factors: Factors contributing to uncertainty.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    level: float = Field(
        ge=0.0,
        le=1.0,
        description="Point confidence estimate",
    )
    range_lower: float = Field(
        ge=0.0,
        le=1.0,
        description="Lower bound of confidence range",
    )
    range_upper: float = Field(
        ge=0.0,
        le=1.0,
        description="Upper bound of confidence range",
    )
    assumptions: tuple[str, ...] = Field(
        default=(),
        description="Key assumptions",
    )
    uncertainty_factors: tuple[str, ...] = Field(
        default=(),
        description="Uncertainty factors",
    )

    @model_validator(mode="after")
    def _validate_range_ordering(self) -> Self:
        """Ensure range_lower <= level <= range_upper.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If ``range_lower > level`` or
                ``level > range_upper``.
        """
        if self.range_lower > self.level:
            msg = (
                f"range_lower ({self.range_lower}) must not exceed level ({self.level})"
            )
            raise ValueError(msg)
        if self.level > self.range_upper:
            msg = (
                f"level ({self.level}) must not exceed range_upper ({self.range_upper})"
            )
            raise ValueError(msg)
        return self


class LensAttribution(BaseModel):
    """Attribution of an insight to a specific strategic lens.

    Attributes:
        lens: Name of the strategic lens that produced this insight.
        insight: The insight or recommendation from this lens.
        weight: How much this lens influenced the final recommendation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    lens: NotBlankStr = Field(description="Strategic lens name")
    insight: NotBlankStr = Field(description="Insight from this lens")
    weight: float = Field(
        ge=0.0,
        le=1.0,
        description="Influence weight in final recommendation",
    )
