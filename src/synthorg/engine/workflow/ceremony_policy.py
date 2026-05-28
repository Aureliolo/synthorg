"""Ceremony scheduling policy configuration and resolution.

Provides the ``CeremonyPolicyConfig`` model (used at project, department,
and ceremony levels) and the ``resolve_ceremony_policy()`` function that
performs field-by-field 3-level resolution.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.engine.workflow.velocity_types import VelocityCalcType
from synthorg.observability import get_logger
from synthorg.observability.events.workflow import (
    SPRINT_CEREMONY_POLICY_CONFIG_CONFLICT,
    SPRINT_CEREMONY_POLICY_RESOLVED,
)
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import (
    MirrorField,
    apply_settings_mirrors,
    parse_bool,
    parse_float,
)

logger = get_logger(__name__)


class CeremonyStrategyType(StrEnum):
    """Supported ceremony scheduling strategies.

    Each maps to a ``CeremonySchedulingStrategy`` implementation.

    Members:
        TASK_DRIVEN: Ceremonies at task-count milestones.
        CALENDAR: Traditional time-based scheduling.
        HYBRID: Calendar + task-driven, whichever fires first.
        EVENT_DRIVEN: Ceremonies subscribe to engine events with debounce.
        BUDGET_DRIVEN: Ceremonies at cost-consumption thresholds.
        THROUGHPUT_ADAPTIVE: Ceremonies when throughput rate changes.
        EXTERNAL_TRIGGER: Ceremonies on external signals.
        MILESTONE_DRIVEN: Ceremonies at semantic project milestones.
    """

    TASK_DRIVEN = "task_driven"
    CALENDAR = "calendar"
    HYBRID = "hybrid"
    EVENT_DRIVEN = "event_driven"
    BUDGET_DRIVEN = "budget_driven"
    THROUGHPUT_ADAPTIVE = "throughput_adaptive"
    EXTERNAL_TRIGGER = "external_trigger"
    MILESTONE_DRIVEN = "milestone_driven"


# -- Trigger constants (shared between scheduler and strategies) -------------

TRIGGER_SPRINT_START: str = "sprint_start"
TRIGGER_SPRINT_END: str = "sprint_end"
TRIGGER_SPRINT_MIDPOINT: str = "sprint_midpoint"
TRIGGER_EVERY_N: str = "every_n_completions"
TRIGGER_SPRINT_PERCENTAGE: str = "sprint_percentage"

# -- Defaults ----------------------------------------------------------------

_DEFAULT_STRATEGY: CeremonyStrategyType = CeremonyStrategyType.TASK_DRIVEN
_DEFAULT_AUTO_TRANSITION: bool = True
_DEFAULT_TRANSITION_THRESHOLD: float = 1.0

STRATEGY_DEFAULT_VELOCITY_CALC: Mapping[CeremonyStrategyType, VelocityCalcType] = (
    MappingProxyType(
        {
            CeremonyStrategyType.TASK_DRIVEN: VelocityCalcType.TASK_DRIVEN,
            CeremonyStrategyType.CALENDAR: VelocityCalcType.CALENDAR,
            CeremonyStrategyType.HYBRID: VelocityCalcType.MULTI_DIMENSIONAL,
            CeremonyStrategyType.EVENT_DRIVEN: VelocityCalcType.POINTS_PER_SPRINT,
            CeremonyStrategyType.BUDGET_DRIVEN: VelocityCalcType.BUDGET,
            CeremonyStrategyType.THROUGHPUT_ADAPTIVE: VelocityCalcType.TASK_DRIVEN,
            CeremonyStrategyType.EXTERNAL_TRIGGER: VelocityCalcType.POINTS_PER_SPRINT,
            CeremonyStrategyType.MILESTONE_DRIVEN: VelocityCalcType.POINTS_PER_SPRINT,
        }
    )
)
"""Strategy-to-velocity-calculator mapping.

Each strategy has a natural default velocity calculator.  Used by
``resolve_ceremony_policy()`` when no level explicitly sets
``velocity_calculator``.
"""


class CeremonyPolicyConfig(BaseModel):
    """Ceremony scheduling policy.

    Appears at project, department, and per-ceremony levels.  ``None``
    fields indicate "inherit from the next level up."  At the project
    level, ``None`` fields fall back to framework defaults.

    Attributes:
        strategy: Scheduling strategy type.
        strategy_config: Strategy-specific configuration parameters.
        velocity_calculator: Velocity calculator type.
        auto_transition: Whether to auto-transition sprints.
        transition_threshold: Fraction of tasks complete to trigger
            auto-transition (0.0, 1.0] -- zero excluded.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="strategy",
            namespace=SettingNamespace.COORDINATION,
            key="ceremony_strategy",
            only_if_env_set=True,
        ),
        MirrorField(
            field="velocity_calculator",
            namespace=SettingNamespace.COORDINATION,
            key="ceremony_velocity_calculator",
            only_if_env_set=True,
        ),
        MirrorField(
            field="auto_transition",
            namespace=SettingNamespace.COORDINATION,
            key="ceremony_auto_transition",
            parse=parse_bool,
            only_if_env_set=True,
        ),
        MirrorField(
            field="transition_threshold",
            namespace=SettingNamespace.COORDINATION,
            key="ceremony_transition_threshold",
            parse=parse_float,
            only_if_env_set=True,
        ),
    )

    strategy: CeremonyStrategyType | None = Field(
        default=None,
        description="Scheduling strategy type",
    )
    strategy_config: Mapping[str, Any] | None = Field(
        default=None,
        description="Strategy-specific configuration parameters",
    )
    velocity_calculator: VelocityCalcType | None = Field(
        default=None,
        description="Velocity calculator type",
    )
    auto_transition: bool | None = Field(
        default=None,
        description="Whether to auto-transition sprints",
    )
    transition_threshold: float | None = Field(
        default=None,
        gt=0.0,
        le=1.0,
        description="Fraction of tasks complete to trigger auto-transition",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: Any) -> Any:
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)


class ResolvedCeremonyPolicy(BaseModel):
    """Fully resolved ceremony policy with no ``None`` fields.

    Produced by ``resolve_ceremony_policy()`` after merging project,
    department, and ceremony levels.

    Attributes:
        strategy: Resolved scheduling strategy type.
        strategy_config: Resolved strategy-specific configuration.
        velocity_calculator: Resolved velocity calculator type.
        auto_transition: Resolved auto-transition flag.
        transition_threshold: Resolved transition threshold.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: CeremonyStrategyType = Field(
        description="Resolved scheduling strategy type",
    )
    strategy_config: Mapping[str, Any] = Field(
        description="Resolved strategy-specific configuration",
    )
    velocity_calculator: VelocityCalcType = Field(
        description="Resolved velocity calculator type",
    )
    auto_transition: bool = Field(
        description="Resolved auto-transition flag",
    )
    transition_threshold: float = Field(
        gt=0.0,
        le=1.0,
        description="Resolved transition threshold",
    )

    @model_validator(mode="after")
    def _validate_threshold_with_auto_transition(self) -> Self:
        """Warn if threshold is set but auto-transition is disabled.

        Returns:
            ``self`` unchanged; the mismatch only emits a warning
            (kept non-fatal so legacy configs still load).
        """
        if not self.auto_transition and self.transition_threshold != 1.0:
            logger.warning(
                SPRINT_CEREMONY_POLICY_CONFIG_CONFLICT,
                note="transition_threshold is set but auto_transition is disabled",
                transition_threshold=self.transition_threshold,
            )
        return self


def resolve_ceremony_policy(
    project: CeremonyPolicyConfig,
    department: CeremonyPolicyConfig | None = None,
    ceremony: CeremonyPolicyConfig | None = None,
) -> ResolvedCeremonyPolicy:
    """Resolve a ceremony policy from 3 levels.

    Field-by-field resolution order (most specific wins):
    ``ceremony ?? department ?? project ?? framework default``.

    Args:
        project: Project-level policy (always required).
        department: Optional department-level override.
        ceremony: Optional per-ceremony override.

    Returns:
        A fully resolved policy with no ``None`` fields.
    """
    layers = [project]
    if department is not None:
        layers.append(department)
    if ceremony is not None:
        layers.append(ceremony)

    # Resolve each field: iterate layers from most specific (last) to
    # least specific (first), taking the first non-None value.
    strategy = _resolve_field(layers, "strategy", _DEFAULT_STRATEGY)
    strategy_config: Mapping[str, Any] = _resolve_field(layers, "strategy_config", {})
    default_vel_calc = STRATEGY_DEFAULT_VELOCITY_CALC[strategy]
    velocity_calculator = _resolve_field(
        layers,
        "velocity_calculator",
        default_vel_calc,
    )
    auto_transition = _resolve_field(
        layers,
        "auto_transition",
        _DEFAULT_AUTO_TRANSITION,
    )
    transition_threshold = _resolve_field(
        layers,
        "transition_threshold",
        _DEFAULT_TRANSITION_THRESHOLD,
    )

    resolved = ResolvedCeremonyPolicy(
        strategy=strategy,
        strategy_config=strategy_config,
        velocity_calculator=velocity_calculator,
        auto_transition=auto_transition,
        transition_threshold=transition_threshold,
    )

    logger.info(
        SPRINT_CEREMONY_POLICY_RESOLVED,
        strategy=resolved.strategy.value,
        velocity_calculator=resolved.velocity_calculator.value,
        auto_transition=resolved.auto_transition,
        transition_threshold=resolved.transition_threshold,
        levels_evaluated=len(layers),
    )
    return resolved


def _resolve_field[T](
    layers: list[CeremonyPolicyConfig],
    field_name: str,
    default: T,
) -> T:
    """Resolve a single field from layers (most specific last).

    Iterates from the last layer (most specific) to the first,
    returning the first non-``None`` value.

    Returns:
        The first non-``None`` field value walking layers
        most-specific-first; ``default`` when every layer is unset.
    """
    for layer in reversed(layers):
        value = getattr(layer, field_name)
        if value is not None:
            return value  # type: ignore[no-any-return]
    return default
