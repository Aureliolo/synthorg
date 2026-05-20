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

from datetime import datetime  # noqa: TC003 -- required at runtime by Pydantic
from enum import StrEnum
from typing import Final
from uuid import UUID  # noqa: TC003 -- required at runtime by Pydantic

from pydantic import BaseModel, ConfigDict, Field

from synthorg.budget.currency import CurrencyCode  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001

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
