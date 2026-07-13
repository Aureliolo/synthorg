"""Autonomy change-strategy plugin config + deps bundle.

A ``StrEnum`` discriminator + frozen Pydantic config, with the safe
``HUMAN_ONLY`` default behaving identically to a bare
``HumanOnlyPromotionStrategy()``. Runtime signal providers live in
:class:`AutonomyStrategyDeps`, not the frozen config.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.security.autonomy.protocol import AutonomyChangeStrategy
from synthorg.security.autonomy.signals import RiskBudgetSignalProvider

_DEFAULT_BUDGET_WARN_FRACTION: Final[float] = 0.2


class AutonomyStrategyType(StrEnum):
    """Discriminator selecting the autonomy change strategy.

    - ``HUMAN_ONLY`` -- promotions + recovery always require human
      approval; byte-identical to a bare ``HumanOnlyPromotionStrategy()``.
    - ``BUDGET_AWARE`` -- denies promotion while risk-budget headroom
      is below the warn fraction; otherwise delegates to the base.
    - ``ESCALATION_CHAIN`` -- promotion is routed through a configured
      role chain; returns ``False`` (pending) until the chain approves.
    """

    HUMAN_ONLY = "human_only"
    BUDGET_AWARE = "budget_aware"
    ESCALATION_CHAIN = "escalation_chain"


class AutonomyStrategyConfig(BaseModel):
    """Operator-tunable autonomy change-strategy configuration.

    Default-constructed (``kind=HUMAN_ONLY``) is byte-identical with
    a bare ``HumanOnlyPromotionStrategy()``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    kind: AutonomyStrategyType = AutonomyStrategyType.HUMAN_ONLY
    # BUDGET_AWARE: deny promotion while headroom is below this.
    budget_warn_fraction: float = Field(
        default=_DEFAULT_BUDGET_WARN_FRACTION, ge=0.0, le=1.0
    )
    # ESCALATION_CHAIN: ordered approver roles (empty == always pending).
    escalation_chain: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AutonomyStrategyDeps:
    """Runtime collaborators the frozen config cannot carry.

    Attributes:
        base: The strategy that downgrade / recovery / override-store
            operations delegate to; defaults to a fresh
            ``HumanOnlyPromotionStrategy`` when the factory is not
            given one.
        risk_budget_signal: REQUIRED for ``BUDGET_AWARE``.
    """

    base: AutonomyChangeStrategy | None = None
    risk_budget_signal: RiskBudgetSignalProvider | None = None
