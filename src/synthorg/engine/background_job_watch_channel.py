"""The state channel ``AgentContext`` carries for watched background jobs.

A leaf module (no ``AgentContext`` import): ``context.py`` imports the
channel type directly, and ``background_job_watch.py`` (the watcher
itself, which needs ``AgentContext`` for its own type hints) imports it
from here too, mirroring how ``AsyncTaskStateChannel`` lives in
``communication/async_tasks/models.py`` rather than inside ``context.py``
or a module that imports it back.
"""

from datetime import datetime
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class WatchedJobRecord(BaseModel):
    """One background job the loop is watching for staleness.

    Attributes:
        job_id: The background job's own id, as returned by
            ``shell_command(background=True)``.
        started_watching_at: When the loop first observed this job.
        last_nudged_at: When the agent was last nudged about this job,
            or ``None`` if never.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    job_id: NotBlankStr = Field(description="Background job id")
    started_watching_at: AwareDatetime = Field(
        description="When the loop first observed this job"
    )
    last_nudged_at: AwareDatetime | None = Field(
        default=None,
        description="When the agent was last nudged about this job",
    )


class BackgroundJobWatchChannel(BaseModel):
    """Dedicated state channel for watched background jobs.

    Separate from ``AgentContext.conversation`` -- not touched by
    compaction. Survives context reset by the same structural guarantee
    as ``async_task_state``.

    Attributes:
        records: Watched job records, keyed uniquely by ``job_id``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    records: tuple[WatchedJobRecord, ...] = Field(
        default=(),
        description="Watched background job records",
    )

    @model_validator(mode="after")
    def _validate_job_id_uniqueness(self) -> Self:
        """Ensure job_ids are unique within the channel.

        Returns:
            The validated channel.

        Raises:
            ValueError: If two records share a ``job_id``.
        """
        job_ids = [r.job_id for r in self.records]
        if len(job_ids) != len(set(job_ids)):
            seen: set[str] = set()
            dupes: set[str] = set()
            for jid in job_ids:
                if jid in seen:
                    dupes.add(jid)
                else:
                    seen.add(jid)
            msg = f"Duplicate job_ids in records: {dupes}"
            raise ValueError(msg)
        return self

    def with_record(self, record: WatchedJobRecord) -> BackgroundJobWatchChannel:
        """Add or replace a watched job record.

        Args:
            record: The record to add or replace, keyed by ``job_id``.

        Returns:
            New channel with the record added or replaced.
        """
        existing = tuple(r for r in self.records if r.job_id != record.job_id)
        return self.model_copy(update={"records": (*existing, record)})

    def without_record(self, job_id: str) -> BackgroundJobWatchChannel:
        """Drop a watched job (it has reached a terminal status, or vanished).

        Args:
            job_id: The job to stop watching.

        Returns:
            New channel with the record removed; unchanged if absent.
        """
        remaining = tuple(r for r in self.records if r.job_id != job_id)
        if len(remaining) == len(self.records):
            return self
        return self.model_copy(update={"records": remaining})

    def get(self, job_id: str) -> WatchedJobRecord | None:
        """Look up a watched job record by id.

        Args:
            job_id: Job identifier to look up.

        Returns:
            The matching record, or ``None`` if not found.
        """
        for r in self.records:
            if r.job_id == job_id:
                return r
        return None


def background_job_watched_update(
    channel: BackgroundJobWatchChannel,
    job_id: NotBlankStr,
    *,
    watching_since: datetime,
) -> dict[str, object] | None:
    """Compute the context-field update for watching a new background job.

    Kept beside the channel it builds, rather than on ``AgentContext``
    itself, the same way ``context_disclosure.py`` keeps the tool-load
    update off the context class: a pure function is testable on its own
    and keeps ``context.py`` within its size budget.

    Args:
        channel: The current watch channel.
        job_id: The background job's own id.
        watching_since: When the loop observed the job start.

    Returns:
        The context field update, or ``None`` when the job is already
        tracked (the caller then returns the context unchanged).
    """
    if channel.get(job_id) is not None:
        return None
    record = WatchedJobRecord(job_id=job_id, started_watching_at=watching_since)
    return {"background_job_watch": channel.with_record(record)}


__all__ = [
    "BackgroundJobWatchChannel",
    "WatchedJobRecord",
    "background_job_watched_update",
]
