"""Persisted per-model benchmark-score record.

A :class:`BenchmarkScoreRecord` is the durable row behind a measured
per-model quality score. It carries the same uncertainty band and
provenance fields as :class:`~synthorg.budget.benchmark_protocol.BenchmarkScore`,
plus the ``model_id`` primary key and the recording provenance
(``suite_version`` / ``cassette_sha256``) so a stale cassette is
detectable and a measured score can never be confused with a fabricated
one.

The :class:`~synthorg.budget.benchmark_measured.MeasuredBenchmarkScoreProvider`
reads these rows and projects them onto ``BenchmarkScore`` for the
Pareto / stakes-routing seam.
"""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.budget.benchmark_protocol import BenchmarkScore
from synthorg.core.types import NotBlankStr


class BenchmarkScoreRecord(BaseModel):
    """A measured per-model benchmark score, keyed by ``model_id``.

    Attributes:
        model_id: Canonical model identifier (the repository key).
        score: Calibrated quality score, 0 to 100.
        confidence_lower: Lower bound of the 95 percent confidence band.
        confidence_upper: Upper bound of the 95 percent confidence band.
        source: Provenance identifier. Measured rows use
            ``"benchmark:..."`` so the dashboard badge flips from
            illustrative to measured.
        suite_version: Brief-suite version the score was measured against
            (``sha256:<digest>``), so a score measured on an outdated
            suite is detectable.
        cassette_sha256: Determinism-source digest of the recorded run
            the score was derived from.
        last_updated: When this score was last refreshed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    model_id: NotBlankStr = Field(description="Canonical model id (repository key)")
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
        description='Provenance identifier (e.g. "benchmark:measured-v1")',
    )
    suite_version: NotBlankStr = Field(
        description="Brief-suite version the score was measured against",
    )
    cassette_sha256: NotBlankStr = Field(
        description="Determinism-source digest of the recorded run",
    )
    last_updated: datetime = Field(description="When this score was last refreshed")

    @model_validator(mode="after")
    def _score_within_confidence_band(self) -> Self:
        """The point estimate must lie inside its confidence interval.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If the score falls outside its confidence band.
        """
        if not (self.confidence_lower <= self.score <= self.confidence_upper):
            msg = (
                f"score ({self.score}) must lie within the confidence band"
                f" [{self.confidence_lower}, {self.confidence_upper}]"
            )
            raise ValueError(msg)
        return self

    def to_score(self) -> BenchmarkScore:
        """Project this record onto a provider-facing :class:`BenchmarkScore`.

        Returns:
            The matching ``BenchmarkScore`` (drops the persistence-only
            ``model_id`` / provenance-digest fields).
        """
        return BenchmarkScore(
            score=self.score,
            confidence_lower=self.confidence_lower,
            confidence_upper=self.confidence_upper,
            source=self.source,
            last_updated=self.last_updated,
        )


__all__ = ["BenchmarkScoreRecord"]
