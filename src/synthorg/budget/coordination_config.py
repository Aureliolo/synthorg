"""Configuration models for coordination metrics.

Defines config models for controlling which coordination metrics are
collected, error taxonomy, and orchestration alert thresholds.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr


class CoordinationMetricName(StrEnum):
    """Names of individual coordination metrics."""

    EFFICIENCY = "efficiency"
    OVERHEAD = "overhead"
    ERROR_AMPLIFICATION = "error_amplification"
    MESSAGE_DENSITY = "message_density"
    REDUNDANCY = "redundancy"
    AMDAHL_CEILING = "amdahl_ceiling"
    STRAGGLER_GAP = "straggler_gap"
    TOKEN_SPEEDUP_RATIO = "token_speedup_ratio"  # noqa: S105
    MESSAGE_OVERHEAD = "message_overhead"


class ErrorCategory(StrEnum):
    """Error categories for multi-agent error taxonomy."""

    LOGICAL_CONTRADICTION = "logical_contradiction"
    NUMERICAL_DRIFT = "numerical_drift"
    CONTEXT_OMISSION = "context_omission"
    COORDINATION_FAILURE = "coordination_failure"
    DELEGATION_PROTOCOL_VIOLATION = "delegation_protocol_violation"
    REVIEW_PIPELINE_VIOLATION = "review_pipeline_violation"
    AUTHORITY_BREACH_ATTEMPT = "authority_breach_attempt"


class DetectionScope(StrEnum):
    """Scope of data available to a detector."""

    SAME_TASK = "same_task"
    TASK_TREE = "task_tree"


class DetectorVariant(StrEnum):
    """Variant type for a detector implementation."""

    HEURISTIC = "heuristic"
    LLM_SEMANTIC = "llm_semantic"
    PROTOCOL_CHECK = "protocol_check"
    BEHAVIOR_CHECK = "behavior_check"


# Allowed detection scopes per detector variant.  Heuristic and
# behavior-check detectors work on a single execution; protocol
# checks and LLM semantic variants can operate on the full task
# tree.  Attempting to configure an incompatible (variant, scope)
# pair is a misconfiguration and is rejected at construction time.
_VARIANT_SCOPES: MappingProxyType[DetectorVariant, frozenset[DetectionScope]] = (
    MappingProxyType(
        {
            DetectorVariant.HEURISTIC: frozenset({DetectionScope.SAME_TASK}),
            DetectorVariant.BEHAVIOR_CHECK: frozenset({DetectionScope.SAME_TASK}),
            DetectorVariant.PROTOCOL_CHECK: frozenset(
                {DetectionScope.SAME_TASK, DetectionScope.TASK_TREE},
            ),
            DetectorVariant.LLM_SEMANTIC: frozenset(
                {DetectionScope.SAME_TASK, DetectionScope.TASK_TREE},
            ),
        }
    )
)


class DetectorCategoryConfig(BaseModel):
    """Per-category detector configuration.

    Attributes:
        variants: Detector implementation variants to run.
        scope: Detection scope level.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    variants: tuple[DetectorVariant, ...] = Field(
        default=(DetectorVariant.HEURISTIC,),
        description="Detector implementation variants to run",
    )
    scope: DetectionScope = Field(
        default=DetectionScope.SAME_TASK,
        description="Detection scope level",
    )

    @model_validator(mode="after")
    def _validate_variants(self) -> Self:
        """Ensure variants are non-empty, unique, and scope-compatible.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not self.variants:
            msg = "variants must not be empty"
            raise ValueError(msg)
        if len(self.variants) != len(set(self.variants)):
            msg = "variants must not contain duplicates"
            raise ValueError(msg)
        for variant in self.variants:
            allowed = _VARIANT_SCOPES.get(variant, frozenset())
            if self.scope not in allowed:
                msg = (
                    f"variant {variant.value} does not support scope "
                    f"{self.scope.value} (allowed: "
                    f"{sorted(s.value for s in allowed)})"
                )
                raise ValueError(msg)
        return self


def _default_detectors() -> dict[ErrorCategory, DetectorCategoryConfig]:
    """Build the default detector configuration.

    Enables all 7 categories with safe defaults:
    - Original 4: heuristic variant, SAME_TASK scope
    - Delegation + review: protocol_check variant, TASK_TREE scope
    - Authority breach: behavior_check variant, SAME_TASK scope

    Returns:
        Mapping from ``ErrorCategory`` to ``DetectorCategoryConfig``.
    """
    return {
        ErrorCategory.LOGICAL_CONTRADICTION: DetectorCategoryConfig(),
        ErrorCategory.NUMERICAL_DRIFT: DetectorCategoryConfig(),
        ErrorCategory.CONTEXT_OMISSION: DetectorCategoryConfig(),
        ErrorCategory.COORDINATION_FAILURE: DetectorCategoryConfig(),
        ErrorCategory.DELEGATION_PROTOCOL_VIOLATION: DetectorCategoryConfig(
            variants=(DetectorVariant.PROTOCOL_CHECK,),
            scope=DetectionScope.TASK_TREE,
        ),
        ErrorCategory.REVIEW_PIPELINE_VIOLATION: DetectorCategoryConfig(
            variants=(DetectorVariant.PROTOCOL_CHECK,),
            scope=DetectionScope.TASK_TREE,
        ),
        ErrorCategory.AUTHORITY_BREACH_ATTEMPT: DetectorCategoryConfig(
            variants=(DetectorVariant.BEHAVIOR_CHECK,),
        ),
    }


class ErrorTaxonomyConfig(BaseModel):
    """Configuration for multi-agent error taxonomy tracking.

    The ``detectors`` dict is the single source of truth for which
    categories are active and how they are configured.  The
    ``categories`` computed property derives the active category
    tuple from ``detectors.keys()``.

    Attributes:
        enabled: Whether error taxonomy tracking is enabled.
        detectors: Per-category detector configuration.
        llm_provider_tier: Provider tier for semantic detectors.
        classification_budget_per_task: Max cost per task for
            LLM-backed classification.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether error taxonomy tracking is enabled",
    )
    detectors: Mapping[ErrorCategory, DetectorCategoryConfig] = Field(
        default_factory=_default_detectors,
        description="Per-category detector configuration",
    )
    llm_provider_tier: NotBlankStr = Field(
        default="large",
        description="Provider tier for semantic detectors",
    )
    classification_budget_per_task: float = Field(
        default=0.01,
        ge=0.0,
        description="Max cost per task for LLM classification",
    )
    detector_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=600.0,
        description=(
            "Per-detector timeout (seconds) applied by the pipeline's"
            " isolation wrapper. Bridged from the"
            " engine.classification_detector_timeout_seconds setting;"
            " bounds match the setting definition (1.0-600.0)."
        ),
    )

    @computed_field(
        description="Active error categories",
    )
    @property
    def categories(self) -> tuple[ErrorCategory, ...]:
        """Active error categories derived from detectors dict."""
        return tuple(self.detectors)

    @model_validator(mode="after")
    def _freeze_detectors(self) -> Self:
        """Wrap ``detectors`` in a read-only proxy at construction.

        Returns:
            The instance with ``detectors`` replaced by a ``MappingProxyType``.
        """
        object.__setattr__(self, "detectors", MappingProxyType(dict(self.detectors)))
        return self


# Default orchestration-overhead alert thresholds.  Documented as a
# triplet so the rationale stays adjacent: 30% / 50% / 70% form the
# info / warn / critical cascade with strict ordering enforced by a
# model_validator below.
_DEFAULT_INFO_THRESHOLD: float = 0.30
_DEFAULT_WARN_THRESHOLD: float = 0.50
_DEFAULT_CRITICAL_THRESHOLD: float = 0.70


class OrchestrationAlertThresholds(BaseModel):
    """Thresholds for orchestration overhead alert levels.

    Attributes:
        info: Ratio threshold for INFO alert (default 0.30 -- 30%
            orchestration overhead is informational; visible in
            dashboards but no operator action required).
        warn: Ratio threshold for WARNING alert (default 0.50 -- 50%
            warrants attention; investigate coordination patterns).
        critical: Ratio threshold for CRITICAL alert (default 0.70 --
            70% is paying more than half the request budget on
            orchestration; stop routing and triage).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    info: float = Field(
        default=_DEFAULT_INFO_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="Ratio threshold for INFO alert",
    )
    warn: float = Field(
        default=_DEFAULT_WARN_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="Ratio threshold for WARNING alert",
    )
    critical: float = Field(
        default=_DEFAULT_CRITICAL_THRESHOLD,
        ge=0.0,
        le=1.0,
        description="Ratio threshold for CRITICAL alert",
    )

    @model_validator(mode="after")
    def _validate_threshold_ordering(self) -> Self:
        """Ensure info < warn < critical.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not (self.info < self.warn < self.critical):
            msg = (
                f"Thresholds must be strictly ordered: "
                f"info ({self.info}) < warn ({self.warn}) "
                f"< critical ({self.critical})"
            )
            raise ValueError(msg)
        return self


class CoordinationMetricsConfig(BaseModel):
    """Top-level configuration for coordination metrics collection.

    Attributes:
        enabled: Whether coordination metrics are collected.
        collect: Which metrics to collect.
        error_taxonomy: Error taxonomy tracking configuration.
        orchestration_alerts: Orchestration overhead alert thresholds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether coordination metrics are collected",
    )
    collect: tuple[CoordinationMetricName, ...] = Field(
        default=tuple(CoordinationMetricName),
        description="Which metrics to collect (must be unique)",
    )
    error_taxonomy: ErrorTaxonomyConfig = Field(
        default_factory=ErrorTaxonomyConfig,
        description="Error taxonomy tracking configuration",
    )
    orchestration_alerts: OrchestrationAlertThresholds = Field(
        default_factory=OrchestrationAlertThresholds,
        description="Orchestration overhead alert thresholds",
    )
    quality_erosion_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Structural erosion score that triggers QUALITY_EROSION stagnation",
    )
    quality_erosion_window: int = Field(
        default=10,
        ge=2,
        le=50,
        description="Number of recent turns to analyze for quality erosion",
    )

    @model_validator(mode="after")
    def _validate_unique_collect(self) -> Self:
        """Ensure no duplicate metric names in collect.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if len(self.collect) != len(set(self.collect)):
            msg = "collect must not contain duplicates"
            raise ValueError(msg)
        return self
