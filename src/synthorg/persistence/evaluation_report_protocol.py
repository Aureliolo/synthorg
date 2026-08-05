# module-kind: repository
"""Append-only persistence for the evaluate stage's verdict.

The verdict is the one artefact that decides whether an initiative
delivered, so it needs a durable record independent of the stage that
produces it. Without one, an operator whose initiative did not complete
has ``unmet_count=2`` in a log line and nothing else, and a lost CAS race
throws away a judgement that cost real money and cannot be re-derived
from anything persisted.

Append-only because a verdict is a historical fact. A re-evaluation is a
new attempt with its own row, not an edit of the old one: overwriting
would erase the evidence that the objective was judged and found wanting,
which is exactly what the replan needs to point at.
"""

from datetime import UTC, datetime
from typing import Final, Protocol, override, runtime_checkable
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.evaluation_verdict import CriterionOutcome, CriterionVerdict
from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository

#: Defence in depth against a runaway submission, mirroring the ceiling the
#: evaluate stage enforces when it parses one. Duplicated deliberately: the
#: store must refuse an oversized row whatever produced it, and importing the
#: engine to learn its own bound would invert the layering.
MAX_PERSISTED_VERDICTS: Final[int] = 100


class EvaluationReportRecord(BaseModel):
    """One persisted judgement of one initiative.

    Attributes:
        record_id: Stable primary key.
        plan_id: The plan whose objective was judged.
        project_id: The plan's project, so a project's history reads
            without joining through every plan generation.
        attempt: Which judgement of this plan this is, counting from 1.
            A plan parked without a verdict and re-evaluated later gets a
            second row rather than replacing the first.
        summary: The judge's narrative of what it checked.
        verdicts: One verdict per objective criterion.
        objective_met: Whether every criterion was met. Stored rather
            than derived so a query can filter on the outcome without
            deserialising every verdict blob.
        evaluated_at: When the judgement landed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    record_id: UUID = Field(
        default_factory=uuid4,
        description="Stable primary key",
    )
    plan_id: NotBlankStr = Field(description="Plan whose objective was judged")
    project_id: NotBlankStr = Field(description="Owning project")
    attempt: int = Field(ge=1, description="Which judgement of this plan")
    summary: NotBlankStr = Field(description="The judge's narrative")
    verdicts: tuple[CriterionVerdict, ...] = Field(
        min_length=1,
        max_length=MAX_PERSISTED_VERDICTS,
        description="One verdict per objective criterion",
    )
    objective_met: bool = Field(description="True iff every criterion was met")
    evaluated_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the judgement landed",
    )

    @model_validator(mode="after")
    def _objective_met_agrees_with_verdicts(self) -> EvaluationReportRecord:
        """Refuse a row whose headline contradicts the verdicts under it.

        ``objective_met`` is stored rather than derived so a query can
        filter without deserialising every blob, which makes it the field a
        reader trusts most and the one nothing else would catch drifting. A
        row claiming delivery over an unmet criterion is precisely the lie
        this table exists to make impossible.

        Returns:
            The validated record.

        Raises:
            ValueError: When the flag and the verdicts disagree.
        """
        every_met = all(
            verdict.outcome is CriterionOutcome.MET for verdict in self.verdicts
        )
        if self.objective_met != every_met:
            msg = (
                f"objective_met={self.objective_met} contradicts the verdicts:"
                f" every criterion met is {every_met}"
            )
            raise ValueError(msg)
        return self


class EvaluationReportFilterSpec(BaseModel):
    """Filter spec for :meth:`EvaluationReportRepository.query`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    plan_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single plan",
    )
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single project",
    )


@runtime_checkable
class EvaluationReportRepository(
    AppendOnlyRepository[EvaluationReportRecord, EvaluationReportFilterSpec],
    Protocol,
):
    """Append-only persistence for initiative evaluation verdicts.

    Composes :class:`AppendOnlyRepository`: ``append`` writes one
    immutable judgement, ``query`` returns them newest-first under a
    filter, and ``purge_before`` enforces retention.
    """

    @override
    async def append(  # pyright: ignore[reportIncompatibleMethodOverride] -- domain-specific param name
        self, record: EvaluationReportRecord, /
    ) -> None:
        """Persist one judgement (append-only; duplicate id is a violation)."""
        ...

    @override
    async def query(
        self,
        filter_spec: EvaluationReportFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[EvaluationReportRecord, ...]:
        """Return judgements matching the filter, newest-first."""
        ...

    @override
    async def purge_before(self, threshold: datetime, /) -> int:
        """Delete judgements older than *threshold*; return the row count."""
        ...


__all__ = [
    "EvaluationReportFilterSpec",
    "EvaluationReportRecord",
    "EvaluationReportRepository",
]
