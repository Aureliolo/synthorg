# module-kind: declarative
"""Domain models for externally-sourced model capability evidence.

A :class:`CapabilityScore` is one measurement, by one source, of one model,
on one axis. Three facts travel with every score and none of them is
optional: which source produced it, when the source measured it, and when
we ingested it. A number with no visible origin is not admissible evidence,
because the defect this whole layer exists to correct was a grading nobody
could trace.

Scores are stored per source rather than reduced on write. Two sources that
disagree about a model are recording a real disagreement, and averaging it
at ingest would destroy the only signal an operator has that the grading is
contested.
"""

from typing import Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr

#: What a score measures. A source publishes results per benchmark, and a
#: benchmark asks one kind of question; grouping them onto these three axes
#: is what lets two sources be compared at all. ``general`` is the catch-all
#: for a composite or aggregate index rather than a dumping ground: a source
#: that publishes a single headline number lands here.
CapabilityAxis = Literal["coding", "reasoning", "general"]

CAPABILITY_AXES: Final[tuple[CapabilityAxis, ...]] = (
    "coding",
    "reasoning",
    "general",
)

#: Scores are normalised to 0-100 on ingest so two sources using different
#: native ranges (a 0-1 pass rate, a 0-100 percentage) are comparable. The
#: parser owns the conversion, because only it knows the feed's native range.
SCORE_MIN: Final[float] = 0.0
SCORE_MAX: Final[float] = 100.0


class CapabilityScore(BaseModel):
    """One source's measurement of one model on one axis.

    Attributes:
        source_label: Registry label of the source that published it.
        model_identifier: The model id **as the source names it**, kept
            verbatim. Resolving it to a configured ``(provider, model_id)``
            pair is a separate, deliberately conservative step: storing the
            source's own string means an unresolved row stays inspectable
            instead of vanishing into a failed match.
        axis: What the score measures.
        score: The measurement, normalised to 0-100 by the parser.
        as_of: When the *source* measured it. This is the number an
            operator reads to judge staleness, and it is not the ingest
            time: a feed refreshed today can still be reporting a
            measurement from a year ago.
        ingested_at: When this installation read it.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_label: NotBlankStr = Field(description="Registry label of the source")
    model_identifier: NotBlankStr = Field(description="Model id as the source names it")
    axis: CapabilityAxis = Field(description="What the score measures")
    score: float = Field(
        ge=SCORE_MIN,
        le=SCORE_MAX,
        description="Measurement normalised to 0-100",
    )
    as_of: AwareDatetime = Field(description="When the source measured it")
    ingested_at: AwareDatetime = Field(description="When this installation read it")


CapabilityScoreKey = tuple[NotBlankStr, NotBlankStr, NotBlankStr]
"""Composite primary key: ``(source_label, model_identifier, axis)``."""


__all__ = [
    "CAPABILITY_AXES",
    "SCORE_MAX",
    "SCORE_MIN",
    "CapabilityAxis",
    "CapabilityScore",
    "CapabilityScoreKey",
]
