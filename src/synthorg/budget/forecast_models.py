"""Pre-flight cost forecast domain models.

The :class:`Forecast` row is the durable record of a pre-flight cost
estimate produced by :class:`~synthorg.budget.forecaster.CostForecaster`.
Rows persist via the ``CostForecastRepository`` protocol (in
:mod:`synthorg.persistence.cost_forecast_protocol`) so that operator
decisions (approve / reject) survive a process restart and the audit
history outlives the in-memory cost tracker's TTL window.

The state machine for :class:`ForecastDecision`:

* ``pending`` -> ``approved`` (operator approves; work pipeline dispatches)
* ``pending`` -> ``rejected`` (operator rejects; work item terminates)
* ``pending`` -> ``superseded`` (operator edited the brief before deciding;
  a fresh ``pending`` row is created and the old one is closed)
* ``approved`` / ``rejected`` are terminal; an edited brief on a terminal
  row produces a fresh ``pending`` row rather than transitioning the
  terminal row.
"""

from datetime import datetime
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.currency import CurrencyCode
from synthorg.core.types import NotBlankStr

# Approval-kind discriminator used by the ApprovalGate queue + UI cards
# to distinguish cost-forecast approvals from other approval kinds.
FORECAST_APPROVAL_KIND: Final[str] = "cost_forecast"

# UI-side default suggestion when the operator raises the ceiling after
# a parked run: pre-fill the input with ``accumulated_cost * MULTIPLIER``.
CEILING_RAISE_SUGGESTION_MULTIPLIER: Final[float] = 1.5


class ForecastDecision(StrEnum):
    """Operator decision state for a pre-flight cost forecast."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class HaltContext(BaseModel):
    """Hard-ceiling halt context attached to a forecast.

    Populated when the in-loop ``BudgetChecker`` crosses a run's hard
    ceiling and the engine parks the context; cleared when the operator
    raises the ceiling so the run can resume. Surfaced on the forecast
    read path so the dashboard can render a "run halted: ceiling
    exceeded" banner without consulting the parked-context store (which
    is keyed by approval id, not forecast id).

    Attributes:
        accumulated_cost: Cost accrued when the ceiling was crossed.
        ceiling_amount: The hard ceiling that was crossed.
        currency: ISO 4217 code stamped on both amounts.
        halted_at: When the halt was recorded.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    accumulated_cost: float = Field(
        ge=0.0,
        description="Cost accrued when the ceiling was crossed",
    )
    ceiling_amount: float = Field(
        ge=0.0,
        description="The hard ceiling that was crossed",
    )
    currency: CurrencyCode = Field(
        description="ISO 4217 code stamped on both amounts",
    )
    halted_at: datetime = Field(description="When the halt was recorded")

    @model_validator(mode="after")
    def _accumulated_at_or_above_ceiling(self) -> Self:
        """A halt only exists because the ceiling was crossed.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if self.accumulated_cost < self.ceiling_amount:
            msg = (
                f"halt accumulated_cost ({self.accumulated_cost}) must be"
                f" >= ceiling_amount ({self.ceiling_amount}); a halt is"
                f" recorded only when the ceiling is crossed"
            )
            raise ValueError(msg)
        return self


class Forecast(BaseModel):
    """A pre-flight cost forecast row.

    Produced by :class:`CostForecaster` from a brief signal and a
    role-skeleton; persisted by :class:`CostForecastRepository`. The
    work-entry adapter consults the row's ``decision`` before
    dispatching the brief into the work pipeline.

    Attributes:
        forecast_id: Stable UUID primary key.
        brief_hash: SHA-256 hex digest of canonical JSON of
            ``(brief_text, role_skeleton, model_assignments, currency)``.
            Edits to the brief produce a new hash; the prior pending
            row is marked ``superseded`` and a fresh pending row is
            created.
        estimated_cost: Mid-point cost estimate in ``currency``.
        lower_bound: Lower bound of the cost estimate (uncertainty
            band). Always ``<= estimated_cost``.
        upper_bound: Upper bound of the cost estimate. Always
            ``>= estimated_cost``.
        currency: ISO 4217 code stamped on the estimate and the
            ceiling. Repo write-path enforces same-currency invariant
            against the live ``budget.currency`` setting.
        decision: Operator decision state.
        decided_at: When the operator decided (``None`` while pending).
        decided_by: Identifier of the deciding operator (``None``
            while pending). Free-form string so the same column can
            carry an agent id when programmatic intake decides.
        ceiling_amount: Per-run hard ceiling the operator approved
            (``None`` when the global ``budget.run_hard_ceiling``
            applies, or when no ceiling is requested).
        created_at: Row creation timestamp.
        updated_at: Last decision-state mutation timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    forecast_id: UUID = Field(description="Stable UUID primary key")
    brief_hash: NotBlankStr = Field(
        description="SHA-256 hex digest of canonical brief JSON",
    )
    estimated_cost: float = Field(
        ge=0.0,
        description="Mid-point cost estimate in `currency`",
    )
    lower_bound: float = Field(
        ge=0.0,
        description="Lower bound of the cost estimate",
    )
    upper_bound: float = Field(
        ge=0.0,
        description="Upper bound of the cost estimate",
    )
    currency: CurrencyCode = Field(
        description="ISO 4217 currency code stamped on the estimate",
    )
    decision: ForecastDecision = Field(
        default=ForecastDecision.PENDING,
        description="Operator decision state",
    )
    decided_at: datetime | None = Field(
        default=None,
        description="When the operator decided (None while pending)",
    )
    decided_by: NotBlankStr | None = Field(
        default=None,
        description="Identifier of the deciding operator",
    )
    ceiling_amount: float | None = Field(
        default=None,
        ge=0.0,
        description="Per-run hard ceiling the operator approved",
    )
    halt_context: HaltContext | None = Field(
        default=None,
        description=(
            "Hard-ceiling halt context; set when the run is parked on a"
            " ceiling crossing, cleared when the operator raises the ceiling"
        ),
    )
    created_at: datetime = Field(description="Row creation timestamp")
    updated_at: datetime = Field(description="Last decision-state mutation timestamp")

    @model_validator(mode="after")
    def _estimate_within_band(self) -> Self:
        """Mirror the DB CHECK so bad estimates fail at construction.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not (self.lower_bound <= self.estimated_cost <= self.upper_bound):
            msg = (
                f"estimated_cost ({self.estimated_cost}) must lie within"
                f" [{self.lower_bound}, {self.upper_bound}]"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _decision_timestamp_invariant(self) -> Self:
        """Mirror the DB chk_cf_decision_timestamp constraint.

        ``pending`` carries neither timestamp nor operator; ``superseded``
        is a system transition (timestamp set, operator NULL);
        ``approved`` / ``rejected`` are operator decisions (both set).
        ``decided_by`` absence -- not ``decided_at`` -- distinguishes a
        system supersede from an operator decision.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        decided = self.decided_at is not None
        attributed = self.decided_by is not None
        if self.decision is ForecastDecision.PENDING:
            consistent = not decided and not attributed
        elif self.decision is ForecastDecision.SUPERSEDED:
            consistent = decided and not attributed
        else:
            consistent = decided and attributed
        if not consistent:
            msg = (
                f"decision {self.decision.value!r} is inconsistent with"
                f" decided_at={self.decided_at!r} /"
                f" decided_by={self.decided_by!r}"
            )
            raise ValueError(msg)
        return self
