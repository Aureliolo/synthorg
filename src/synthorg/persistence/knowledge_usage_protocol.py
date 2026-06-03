# module-kind: declarative
"""Knowledge-usage capture model and repository protocol.

A ``KnowledgeUsageRecord`` is written once per retrieved knowledge hit
during an agent run, keyed by ``execution_id``. The deliverable-receipt
builder queries these rows to enumerate the distinct sources a run
consulted, then re-resolves each against the knowledge-source registry.

The model lives here (alongside its protocol) rather than in the
``deliverable_receipts`` feature package so the low-level capture sink
(``KnowledgeService.search``) imports it from persistence, not from a
feature it sits below.
"""

from datetime import UTC, datetime
from typing import Protocol, override, runtime_checkable
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, AppendOnlyRepository

__all__ = [
    "KnowledgeUsageFilterSpec",
    "KnowledgeUsageRecord",
    "KnowledgeUsageRecordRepository",
]


class KnowledgeUsageRecord(BaseModel):
    """One knowledge source consulted during a run.

    Written at retrieval time so the receipt builder can reconstruct
    every source the run touched, not only those later cited. Multiple
    hits on the same ``source_id`` produce multiple rows; the builder
    dedupes by ``source_id`` at assembly time.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    record_id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique record identifier",
    )
    task_id: NotBlankStr = Field(description="Task the run was working on")
    execution_id: NotBlankStr = Field(description="Execution run identifier")
    project_id: NotBlankStr = Field(description="Owning project")
    source_id: NotBlankStr = Field(description="Knowledge source identifier")
    chunk_id: NotBlankStr = Field(description="Retrieved chunk identifier")
    content_hash: NotBlankStr = Field(
        description="SHA-256 of source content at capture",
    )
    recorded_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the retrieval was recorded",
    )


class KnowledgeUsageFilterSpec(BaseModel):
    """Filter spec for :meth:`KnowledgeUsageRecordRepository.query`."""

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
    source_id: NotBlankStr | None = Field(
        default=None,
        description="Filter to a single source",
    )


@runtime_checkable
class KnowledgeUsageRecordRepository(
    AppendOnlyRepository[KnowledgeUsageRecord, KnowledgeUsageFilterSpec],
    Protocol,
):
    """Append-only persistence for knowledge-usage records.

    Composes :class:`AppendOnlyRepository`: ``append`` writes one
    immutable record, ``query`` returns records newest-first under a
    filter, and ``purge_before`` enforces retention.
    """

    @override
    async def append(  # pyright: ignore[reportIncompatibleMethodOverride] -- domain-specific param name
        self,
        record: KnowledgeUsageRecord,
    ) -> None:
        """Persist one usage record (append-only; duplicate id is a violation)."""
        ...

    @override
    async def query(
        self,
        filter_spec: KnowledgeUsageFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[KnowledgeUsageRecord, ...]:
        """Return records matching the filter, newest-first."""
        ...

    @override
    async def purge_before(self, threshold: datetime) -> int:
        """Delete records with ``recorded_at < threshold``. Returns rows removed.

        ``threshold`` must be timezone-aware; a naive datetime is rejected
        (``QueryError``) rather than silently coerced, so a wrong-timezone
        threshold cannot delete the wrong retention window.
        """
        ...
