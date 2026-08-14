# module-kind: code
"""Per-source ingest status: what happened last time, and how long ago.

Kept separate from the scores themselves because the two answer different
questions. The scores say what a source measured; this says whether the
source is still answering. A source whose fetch has been failing for a
month still has yesterday's scores in the table, and without this record
the grading built on them looks exactly as healthy as one refreshed an
hour ago.

That is also why a failed refresh never clears a source's rows: the last
good evidence keeps grading, visibly ageing, until something better
arrives.
"""

from datetime import datetime, timedelta

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class CapabilitySourceStatus(BaseModel):
    """The outcome of one source's most recent ingest attempt.

    Attributes:
        source_label: Registry label this status belongs to.
        last_attempted_at: When a refresh was last tried, successful or
            not. The age gate reads this rather than the success time, so
            a source that is failing retries on the same cadence as one
            that is working instead of on every request.
        last_succeeded_at: When a refresh last produced usable rows.
            ``None`` means this source has never worked here.
        last_error: Why the last attempt failed, redacted, or empty when
            it succeeded.
        rows_read: Data rows the last successful parse saw.
        rows_skipped: Rows it could not use. Large next to ``rows_read``,
            this is a feed whose shape has moved.
        scores_written: Measurements the last success persisted.
        feed_url: What was actually fetched, which may be an operator's
            URL rather than the registry default.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_label: NotBlankStr = Field(description="Registry label")
    last_attempted_at: AwareDatetime | None = Field(
        default=None,
        description="When a refresh was last tried",
    )
    last_succeeded_at: AwareDatetime | None = Field(
        default=None,
        description="When a refresh last produced rows",
    )
    last_error: str = Field(
        default="",
        description="Redacted failure reason, empty when healthy",
    )
    rows_read: int = Field(default=0, ge=0, description="Rows the parse saw")
    rows_skipped: int = Field(default=0, ge=0, description="Rows it could not use")
    scores_written: int = Field(default=0, ge=0, description="Measurements persisted")
    feed_url: str = Field(default="", description="What was actually fetched")

    @property
    def is_healthy(self) -> bool:
        """Whether the last attempt produced usable evidence.

        Returns:
            ``True`` when the most recent attempt succeeded.
        """
        return not self.last_error and self.last_succeeded_at is not None

    def is_due(self, *, now: datetime, interval: timedelta) -> bool:
        """Whether this source is old enough to refresh.

        Returns:
            ``True`` when nothing has been attempted yet, or the last
            attempt is at least *interval* old.
        """
        if self.last_attempted_at is None:
            return True
        return now - self.last_attempted_at >= interval


__all__ = ["CapabilitySourceStatus"]
