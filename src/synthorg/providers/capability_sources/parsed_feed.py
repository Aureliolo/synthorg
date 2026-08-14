# module-kind: declarative
"""What one parse of one feed produced, including what it could not use.

A parser returns counts as well as scores because "this source graded two
of your forty models" and "this source is working" are different
statements, and only the first is actionable. A feed that silently drops
nineteen rows in twenty looks healthy from the outside; the skipped count
is what makes a mis-shaped feed or a bad identifier mapping visible before
an operator starts wondering why their model is still heuristic-graded.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.providers.capability_sources.models import CapabilityScore


class ParsedFeed(BaseModel):
    """The scores one parse produced, and the rows it could not use.

    Attributes:
        scores: The usable measurements, one per
            ``(model_identifier, axis)`` the feed covered.
        rows_read: How many data rows the document contained.
        rows_skipped: How many were unusable (a blank identifier, an
            unparseable number, a benchmark no axis claims, or a row the
            source did not measure itself). Never an error on its
            own: feeds legitimately carry rows about things we do not
            grade, so this is a signal to surface rather than a failure
            to raise.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    scores: tuple[CapabilityScore, ...] = Field(
        default=(),
        description="Usable measurements produced by this parse",
    )
    rows_read: int = Field(ge=0, description="Data rows the document contained")
    rows_skipped: int = Field(ge=0, description="Rows the parser could not use")


__all__ = ["ParsedFeed"]
