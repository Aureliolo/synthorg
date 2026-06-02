# module-kind: declarative
"""Code-execution capture model and repository protocol.

A ``CodeExecutionRecord`` is written by the code-runner tool when the
agent marks an execution with a non-default :class:`CodeExecutionPurpose`
(today, ``TESTS``). The deliverable-receipt builder queries the
``TESTS``-purpose rows for a run to populate the receipt's test section,
so claimed test results always reconcile against a persisted record.

The model lives here (alongside its protocol) so the code-runner tool
imports it from persistence rather than from the feature package above
it. ``CodeExecutionPurpose`` is defined here rather than in
``core/enums.py`` because that module is a net-shrink god-module.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, Self, override, runtime_checkable
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository

__all__ = [
    "CodeExecutionFilterSpec",
    "CodeExecutionPurpose",
    "CodeExecutionRecord",
    "CodeExecutionRecordRepository",
]


class CodeExecutionPurpose(StrEnum):
    """Why the agent ran code in the sandbox.

    ``GENERAL`` executions are not persisted (they are routine work);
    ``TESTS`` executions are captured so a deliverable receipt can claim
    test results that reconcile against a stored record.
    """

    GENERAL = "general"
    TESTS = "tests"


class CodeExecutionRecord(BaseModel):
    """One persisted code execution (a test run) during a deliverable run."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    record_id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique record identifier",
    )
    task_id: NotBlankStr = Field(description="Task the run was working on")
    execution_id: NotBlankStr = Field(description="Execution run identifier")
    project_id: NotBlankStr = Field(description="Owning project")
    purpose: CodeExecutionPurpose = Field(description="Why the code was run")
    command: NotBlankStr = Field(description="Command that was executed")
    returncode: int = Field(description="Process exit code")
    passed: bool = Field(description="True iff returncode 0 and not timed out")
    timed_out: bool = Field(description="Whether the run hit its time limit")
    stdout_tail: str | None = Field(
        default=None,
        description="Tail of captured stdout, length-bounded at capture",
    )
    stderr_tail: str | None = Field(
        default=None,
        description="Tail of captured stderr, length-bounded at capture",
    )
    executed_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the run finished",
    )

    @model_validator(mode="after")
    def _passed_is_consistent(self) -> Self:
        """``passed`` must be True iff exit code 0 and not timed out.

        Returns:
            The validated record.

        Raises:
            ValueError: If ``passed`` disagrees with the run outcome --
                either True while the run had a non-zero exit code or
                timed out, or False while the run exited 0 and did not
                time out.
        """
        expected_passed = self.returncode == 0 and not self.timed_out
        if self.passed != expected_passed:
            msg = "passed must be True iff returncode==0 and timed_out=False"
            raise ValueError(msg)
        return self


class CodeExecutionFilterSpec(BaseModel):
    """Filter spec for :meth:`CodeExecutionRecordRepository.query`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single execution",
    )
    task_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single task",
    )
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single project",
    )
    purpose: CodeExecutionPurpose | None = Field(
        default=None,
        description="Filter to a single execution purpose",
    )


@runtime_checkable
class CodeExecutionRecordRepository(
    AppendOnlyRepository[CodeExecutionRecord, CodeExecutionFilterSpec],
    Protocol,
):
    """Append-only persistence for code-execution (test) records.

    Composes :class:`AppendOnlyRepository`: ``append`` writes one
    immutable record, ``query`` returns records newest-first under a
    filter, and ``purge_before`` enforces retention.
    """

    @override
    async def append(  # pyright: ignore[reportIncompatibleMethodOverride] -- domain-specific param name
        self,
        record: CodeExecutionRecord,
    ) -> None:
        """Persist one execution record (append-only; duplicate id is a violation)."""
        ...

    @override
    async def query(
        self,
        filter_spec: CodeExecutionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CodeExecutionRecord, ...]:
        """Return records matching the filter, newest-first."""
        ...

    @override
    async def purge_before(self, threshold: datetime) -> int:
        """Delete records with ``executed_at < threshold``. Returns rows removed."""
        ...
