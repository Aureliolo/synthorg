"""Domain models for the async task protocol.

Defines the state channel, task records, and status enum
for supervisor-facing async task management.
"""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class AsyncTaskStatus(StrEnum):
    """Status of an async task from the supervisor's perspective."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AsyncTaskRecord(BaseModel):
    """A single tracked async task in the state channel.

    Attributes:
        task_id: Unique task identifier from TaskEngine.
        agent_name: Name of the agent executing this task.
        subtask_id: Optional subtask identifier for decomposed tasks.
        status: Current status from the supervisor's perspective.
        created_at: When the task was started.
        updated_at: When the status was last updated.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr = Field(description="Task identifier")
    agent_name: NotBlankStr = Field(description="Executing agent name")
    subtask_id: NotBlankStr | None = Field(
        default=None,
        description="Optional subtask identifier",
    )
    status: AsyncTaskStatus = Field(description="Current task status")
    created_at: AwareDatetime = Field(description="When task was started")
    updated_at: AwareDatetime = Field(description="Last status update")

    @model_validator(mode="after")
    def _validate_timestamp_order(self) -> Self:
        """Ensure updated_at >= created_at.

        Returns:
            The validated record.

        Raises:
            ValueError: If ``updated_at`` precedes ``created_at``.
        """
        if self.updated_at < self.created_at:
            msg = (
                f"updated_at ({self.updated_at}) must be >= "
                f"created_at ({self.created_at})"
            )
            raise ValueError(msg)
        return self


class TaskSpec(BaseModel):
    """Specification for a new async task.

    What the supervisor wants a subagent to do.

    Attributes:
        title: Human-readable task title.
        description: Detailed task description.
        agent_id: Target agent to execute this task.
        parent_task_id: Supervisor's own task ID for lineage.
        metadata: Additional key-value metadata.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr = Field(description="Task title")
    description: NotBlankStr = Field(description="Task description")
    agent_id: NotBlankStr = Field(description="Target agent ID")
    parent_task_id: NotBlankStr | None = Field(
        default=None,
        description="Supervisor task ID for lineage",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Additional metadata",
    )


class AsyncTaskStateChannel(BaseModel):
    """Dedicated state channel for tracked async tasks.

    Separate from ``AgentContext.conversation`` -- not touched by
    compaction strategies. Survives context reset by structural
    guarantee.

    All mutations return a new instance via ``model_copy(update=...)``.

    Attributes:
        records: Ordered tuple of tracked task records.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    records: tuple[AsyncTaskRecord, ...] = Field(
        default=(),
        description="Tracked async task records",
    )

    @model_validator(mode="after")
    def _validate_task_id_uniqueness(self) -> Self:
        """Ensure task_ids are unique within the channel.

        Returns:
            The validated channel.

        Raises:
            ValueError: If two records share a ``task_id``.
        """
        task_ids = [r.task_id for r in self.records]
        if len(task_ids) != len(set(task_ids)):
            seen: set[str] = set()
            dupes: set[str] = set()
            for tid in task_ids:
                if tid in seen:
                    dupes.add(tid)
                else:
                    seen.add(tid)
            msg = f"Duplicate task_ids in records: {dupes}"
            raise ValueError(msg)
        return self

    def with_record(
        self,
        record: AsyncTaskRecord,
    ) -> AsyncTaskStateChannel:
        """Add or replace a task record.

        If a record with the same ``task_id`` exists, it is replaced.
        Otherwise the new record is appended.

        Args:
            record: The task record to add or replace.

        Returns:
            New state channel with updated records.
        """
        existing = tuple(r for r in self.records if r.task_id != record.task_id)
        return self.model_copy(
            update={"records": (*existing, record)},
        )

    def with_updated(
        self,
        task_id: str,
        status: AsyncTaskStatus,
        updated_at: AwareDatetime,
    ) -> AsyncTaskStateChannel:
        """Update the status of a tracked task.

        Args:
            task_id: Task to update.
            status: New status.
            updated_at: Timestamp for the update.

        Returns:
            New state channel with the updated record, or unchanged
            if the task_id is not found.

        Raises:
            ValueError: If ``updated_at`` precedes the record's
                ``created_at``.
        """
        new_records: list[AsyncTaskRecord] = []
        for r in self.records:
            if r.task_id == task_id:
                if updated_at < r.created_at:
                    msg = (
                        f"updated_at ({updated_at}) must be >= "
                        f"created_at ({r.created_at})"
                    )
                    raise ValueError(msg)
                new_records.append(
                    r.model_copy(
                        update={
                            "status": status,
                            "updated_at": updated_at,
                        },
                    ),
                )
            else:
                new_records.append(r)
        return self.model_copy(update={"records": tuple(new_records)})

    def get(self, task_id: str) -> AsyncTaskRecord | None:
        """Look up a task record by ID.

        Args:
            task_id: Task identifier to look up.

        Returns:
            The matching record, or ``None`` if not found.
        """
        for r in self.records:
            if r.task_id == task_id:
                return r
        return None
