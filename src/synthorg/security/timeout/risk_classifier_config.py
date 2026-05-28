"""Risk-tier-classifier plugin config + dependency bundle.

A ``StrEnum`` discriminator plus a frozen Pydantic config, with the
safe ``DEFAULT`` byte-identical to a bare
``DefaultRiskTierClassifier()``. Runtime collaborators that do not
belong in frozen config (the in-flight probe callable, the ``Clock``
seam) live in :class:`RiskClassifierDeps`, matching the consolidation
factory's deps-bundle split.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import ApprovalRiskLevel

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.core.clock import Clock
    from synthorg.security.timeout.protocol import RiskTierClassifier

_DEFAULT_WORKLOAD_THRESHOLD: Final[int] = 10
_DEFAULT_OFF_HOURS_START_HOUR: Final[int] = 20
_DEFAULT_OFF_HOURS_END_HOUR: Final[int] = 6
_MAX_HOUR_OF_DAY: Final[int] = 23


class RiskClassifierType(StrEnum):
    """Discriminator selecting the risk-tier-classifier composition.

    - ``DEFAULT`` -- the static action-type -> risk map; unknown
      types fail safe to HIGH.
    - ``WORKLOAD_ADAPTIVE`` -- wraps a base classifier and elevates one
      tier when an injected in-flight probe exceeds a threshold.
    - ``OPERATOR_CONFIGURABLE`` -- an operator-defined
      action-type -> tier map; unknown types fail safe to HIGH.
    - ``TIME_BASED`` -- elevates one tier during configured off-hours /
      weekend windows (uses the ``Clock`` seam).
    """

    DEFAULT = "default"
    WORKLOAD_ADAPTIVE = "workload_adaptive"
    OPERATOR_CONFIGURABLE = "operator_configurable"
    TIME_BASED = "time_based"


class RiskClassifierConfig(BaseModel):
    """Operator-tunable risk-tier-classifier configuration.

    Default-constructed (``kind=DEFAULT``) it is byte-identical with
    a bare ``DefaultRiskTierClassifier()``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: RiskClassifierType = RiskClassifierType.DEFAULT
    # DEFAULT: optional overrides merged onto the static risk map.
    custom_map: dict[str, ApprovalRiskLevel] = Field(default_factory=dict)
    # WORKLOAD_ADAPTIVE: in-flight count at/above this elevates one tier.
    workload_threshold: int = Field(default=_DEFAULT_WORKLOAD_THRESHOLD, ge=1)
    # OPERATOR_CONFIGURABLE: the operator's action-type -> tier taxonomy.
    operator_map: dict[str, ApprovalRiskLevel] = Field(default_factory=dict)
    # TIME_BASED: inclusive off-hours window (24h local) + weekend flag.
    off_hours_start_hour: int = Field(
        default=_DEFAULT_OFF_HOURS_START_HOUR, ge=0, le=_MAX_HOUR_OF_DAY
    )
    off_hours_end_hour: int = Field(
        default=_DEFAULT_OFF_HOURS_END_HOUR, ge=0, le=_MAX_HOUR_OF_DAY
    )
    weekend_elevation: bool = True


@dataclass(frozen=True, slots=True)
class RiskClassifierDeps:
    """Runtime collaborators the frozen config cannot carry.

    Attributes:
        base: Base classifier the ``WORKLOAD_ADAPTIVE`` wrapper
            elevates from; defaults to a fresh ``DEFAULT`` classifier
            when the factory is not given one.
        inflight_probe: Returns the current in-flight request count;
            REQUIRED for ``WORKLOAD_ADAPTIVE`` (factory raises
            :class:`RiskClassifierConfigError` if missing).
        clock: Clock seam for ``TIME_BASED`` window evaluation;
            defaults to ``SystemClock`` when omitted.
    """

    base: RiskTierClassifier | None = None
    inflight_probe: Callable[[], int] | None = None
    clock: Clock | None = None
