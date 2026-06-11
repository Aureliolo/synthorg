"""In-memory fake for ``DecisionRepository`` protocol tests.

Extracted from ``tests/unit/api/fakes.py`` to keep that module under
the 800-line budget.  ``FakeDecisionRepository`` is re-exported from
``fakes`` for backwards compatibility so existing test imports
continue to work.
"""

from copy import deepcopy
from datetime import UTC
from types import MappingProxyType
from uuid import UUID

from pydantic import AwareDatetime

from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.types import NotBlankStr
from synthorg.engine.decisions import DecisionOutcome, DecisionRecord
from synthorg.persistence.decision_protocol import DecisionFilterSpec, DecisionRole

__all__ = ["FakeDecisionRepository"]


class FakeDecisionRepository:
    """In-memory decision record repository for tests.

    Mirrors the SQLite implementation's server-assigned monotonic
    version contract: ``append_with_next_version`` computes the next
    version for each ``task_id`` as ``max(existing.version) + 1``,
    matching the real repo's ``COALESCE(MAX(version), 0) + 1`` SQL.
    Using ``max(...)`` instead of ``len(...)`` keeps the fake
    resilient to tests that seed non-contiguous version histories.

    Also matches the real repo's UTC normalization of ``recorded_at``
    and its ``MappingProxyType``-wrapped metadata view so tests that
    exercise both backends observe identical behavior.
    """

    def __init__(self) -> None:
        self._records: dict[str, DecisionRecord] = {}

    async def append_with_next_version(  # noqa: PLR0913
        self,
        *,
        record_id: NotBlankStr,
        task_id: NotBlankStr,
        approval_id: NotBlankStr | None,
        executing_agent_id: NotBlankStr,
        reviewer_agent_id: NotBlankStr,
        decision: DecisionOutcome,
        reason: str | None,
        criteria_snapshot: tuple[NotBlankStr, ...],
        recorded_at: AwareDatetime,
        metadata: dict[str, object] | None = None,
    ) -> DecisionRecord:
        if record_id in self._records:
            msg = f"Duplicate decision record {record_id!r}"
            raise DuplicateRecordError(msg)
        # Reject naive datetimes explicitly to match the production
        # ``SQLiteDecisionRepository`` contract (see
        # ``synthorg/persistence/sqlite/decision/``).
        if recorded_at.tzinfo is None:
            msg = (
                f"recorded_at must be timezone-aware, got a naive "
                f"datetime for decision record {record_id!r}"
            )
            raise ValueError(msg)
        next_version = (
            max(
                (r.version for r in self._records.values() if r.task_id == task_id),
                default=0,
            )
            + 1
        )
        # Deep-copy the metadata (matching production behavior where
        # ``_build_insert_params`` serializes via json.dumps, yielding
        # an independent snapshot) so mutations to the caller's dict
        # do not leak into the stored record.  Wrapping in
        # ``MappingProxyType`` then blocks direct
        # ``record.metadata["k"] = ...`` mutation.  The
        # ``DecisionRecord`` field validator recursively freezes
        # nested containers; we only need to make sure a shared-ref
        # original dict cannot be aliased here.
        snapshot_metadata: MappingProxyType[str, object] = MappingProxyType(
            deepcopy(dict(metadata or {}))
        )
        # Normalize recorded_at to UTC to match
        # ``SQLiteDecisionRepository.append_with_next_version``; tests
        # that pass a non-UTC timezone-aware datetime should observe
        # the same UTC-normalized value from the fake as from the
        # real repo.
        record = DecisionRecord(
            id=UUID(record_id),
            task_id=task_id,
            approval_id=approval_id,
            executing_agent_id=executing_agent_id,
            reviewer_agent_id=reviewer_agent_id,
            decision=decision,
            reason=reason,
            criteria_snapshot=criteria_snapshot,
            recorded_at=recorded_at.astimezone(UTC),
            version=next_version,
            metadata=snapshot_metadata,
        )
        self._records[record_id] = record
        return record

    async def append(self, event: DecisionRecord) -> None:
        if str(event.id) in self._records:
            msg = f"Duplicate decision record {str(event.id)!r}"
            raise DuplicateRecordError(msg)
        self._records[str(event.id)] = event

    async def query(
        self,
        filter_spec: DecisionFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        results = list(self._records.values())
        if filter_spec.task_id is not None:
            results = [r for r in results if r.task_id == filter_spec.task_id]
        if filter_spec.agent_id is not None and filter_spec.role is None:
            results = [
                r
                for r in results
                if filter_spec.agent_id in {r.executing_agent_id, r.reviewer_agent_id}
            ]
        elif filter_spec.agent_id is not None and filter_spec.role == "executor":
            results = [
                r for r in results if r.executing_agent_id == filter_spec.agent_id
            ]
        elif filter_spec.agent_id is not None and filter_spec.role == "reviewer":
            results = [
                r for r in results if r.reviewer_agent_id == filter_spec.agent_id
            ]
        if filter_spec.task_id is not None:
            results.sort(key=lambda r: (r.recorded_at, r.id))
        else:
            results.sort(key=lambda r: (r.recorded_at, r.id), reverse=True)
        return tuple(results[offset : offset + limit])

    async def get(self, record_id: NotBlankStr) -> DecisionRecord | None:
        return self._records.get(record_id)

    async def list_by_task(
        self,
        task_id: NotBlankStr,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        matching = [r for r in self._records.values() if r.task_id == task_id]
        matching.sort(key=lambda r: r.version)
        return tuple(matching[offset : offset + limit])

    async def list_by_agent(
        self,
        agent_id: NotBlankStr,
        *,
        role: DecisionRole,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[DecisionRecord, ...]:
        if role not in {"executor", "reviewer"}:
            msg = f"role must be 'executor' or 'reviewer', got {role!r}"
            raise ValueError(msg)
        if role == "executor":
            matching = [
                r for r in self._records.values() if r.executing_agent_id == agent_id
            ]
        else:
            matching = [
                r for r in self._records.values() if r.reviewer_agent_id == agent_id
            ]
        matching.sort(key=lambda r: (r.recorded_at, r.id), reverse=True)
        return tuple(matching[offset : offset + limit])

    async def purge_before(self, threshold: AwareDatetime) -> int:
        before = len(self._records)
        self._records = {
            k: r for k, r in self._records.items() if r.recorded_at >= threshold
        }
        return before - len(self._records)
