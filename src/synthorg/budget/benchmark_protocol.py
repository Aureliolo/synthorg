"""Benchmark-score provider protocol for the Pareto view.

The Pareto view answers "90% of the quality at 40% of the cost if you
downgrade these roles". The quality axis comes from a calibrated
per-model benchmark score.

:class:`StubBenchmarkScoreProvider` (in
:mod:`synthorg.budget.benchmark_stub`) returns per-tier calibrated
constants in code; real benchmark implementations swap in behind this
protocol via the factory wiring in ``lifecycle_helpers.py``. The UI
surfaces the :attr:`BenchmarkScore.source` field so operators can
see whether they are reading stub or measured data.
"""

from collections.abc import Mapping  # noqa: TC003 -- required at runtime by Pydantic
from datetime import datetime  # noqa: TC003 -- required at runtime by Pydantic
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001


class BenchmarkScore(BaseModel):
    """Per-model benchmark score with uncertainty band and provenance.

    Attributes:
        score: Calibrated quality score, 0 to 100.
        confidence_lower: Lower bound of the score's 95 percent
            confidence interval.
        confidence_upper: Upper bound of the score's 95 percent
            confidence interval.
        source: Provenance identifier. Stubs use ``"stub:..."`` and
            real benchmark runs use ``"benchmark:..."``; the dashboard
            renders this verbatim so operators can never mistake
            illustrative data for measured data.
        last_updated: When this score was last refreshed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    score: float = Field(ge=0.0, le=100.0, description="Calibrated 0 to 100 score")
    confidence_lower: float = Field(
        ge=0.0,
        le=100.0,
        description="Lower bound of 95 percent confidence interval",
    )
    confidence_upper: float = Field(
        ge=0.0,
        le=100.0,
        description="Upper bound of 95 percent confidence interval",
    )
    source: NotBlankStr = Field(
        description='Provenance identifier (e.g. "stub:calibrated-v1")',
    )
    last_updated: datetime = Field(description="When this score was last refreshed")

    @model_validator(mode="after")
    def _score_within_confidence_band(self) -> Self:
        """The point estimate must lie inside its confidence interval.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        if not (self.confidence_lower <= self.score <= self.confidence_upper):
            msg = (
                f"score ({self.score}) must lie within the confidence band"
                f" [{self.confidence_lower}, {self.confidence_upper}]"
            )
            raise ValueError(msg)
        return self


@runtime_checkable
class BenchmarkScoreProvider(Protocol):
    """Source of per-model benchmark scores for the Pareto view.

    Implementations must be concurrency-safe; the analyzer may call
    :meth:`get_score` and :meth:`list_scores` concurrently from
    multiple coroutines.
    """

    async def get_score(self, model_id: NotBlankStr) -> BenchmarkScore | None:
        """Return the calibrated score for ``model_id``, or ``None``.

        ``None`` signals "no score available for this model"; the
        analyzer must handle this gracefully (skip the role rather
        than emitting an invalid Pareto point).

        Returns:
            The matching ``BenchmarkScore``, or ``None`` when no match is found.
        """
        ...

    async def list_scores(self) -> Mapping[NotBlankStr, BenchmarkScore]:
        """Return all known model scores keyed by canonical model id.

        Returns:
            Result of type ``Mapping[NotBlankStr, BenchmarkScore]``.
        """
        ...
