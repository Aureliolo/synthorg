"""Conformance tests for ``DecisionRepository`` (SQLite + Postgres)."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import DecisionOutcome
from synthorg.core.persistence_errors import QueryError
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decisions import DecisionRecord
from synthorg.persistence.decision_protocol import DecisionFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 3, 15, 10, 0, 0, tzinfo=UTC)


async def _seed_task(backend: PersistenceBackend, task_id: str) -> None:
    """Satisfy the ``decision_records.task_id`` FK by persisting a minimal task row."""
    await backend.tasks.save(
        Task(
            id=as_uuid(task_id),
            title=NotBlankStr(task_id),
            description=NotBlankStr("fixture task"),
            type=TaskType.DEVELOPMENT,
            project=NotBlankStr("proj-conf"),
            created_by=NotBlankStr("system"),
        ),
    )


class TestDecisionRepository:
    async def test_append_and_get(self, backend: PersistenceBackend) -> None:
        await _seed_task(backend, "task-001")
        record = await backend.decision_records.append_with_next_version(
            record_id=NotBlankStr("dec-001"),
            task_id=NotBlankStr(sid("task-001")),
            approval_id=NotBlankStr("appr-001"),
            executing_agent_id=NotBlankStr("agent-exec"),
            reviewer_agent_id=NotBlankStr("agent-rev"),
            decision=DecisionOutcome.APPROVED,
            reason="on-spec",
            criteria_snapshot=(NotBlankStr("tests-pass"),),
            recorded_at=_NOW,
        )
        assert record.version == 1

        fetched = await backend.decision_records.get(NotBlankStr("dec-001"))
        assert fetched is not None
        assert fetched.decision == DecisionOutcome.APPROVED
        assert fetched.task_id == sid("task-001")

    async def test_append_assigns_next_version_per_task(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_task(backend, "same-task")
        first = await backend.decision_records.append_with_next_version(
            record_id=NotBlankStr("d1"),
            task_id=NotBlankStr(sid("same-task")),
            approval_id=None,
            executing_agent_id=NotBlankStr("a"),
            reviewer_agent_id=NotBlankStr("b"),
            decision=DecisionOutcome.APPROVED,
            reason=None,
            criteria_snapshot=(),
            recorded_at=_NOW,
        )
        second = await backend.decision_records.append_with_next_version(
            record_id=NotBlankStr("d2"),
            task_id=NotBlankStr(sid("same-task")),
            approval_id=None,
            executing_agent_id=NotBlankStr("a"),
            reviewer_agent_id=NotBlankStr("b"),
            decision=DecisionOutcome.REJECTED,
            reason="drift",
            criteria_snapshot=(),
            recorded_at=_NOW,
        )
        assert first.version == 1
        assert second.version == 2

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.decision_records.get(NotBlankStr("ghost")) is None

    async def test_list_by_task(self, backend: PersistenceBackend) -> None:
        await _seed_task(backend, "t")
        await backend.decision_records.append_with_next_version(
            record_id=NotBlankStr("a"),
            task_id=NotBlankStr(sid("t")),
            approval_id=None,
            executing_agent_id=NotBlankStr("exec"),
            reviewer_agent_id=NotBlankStr("rev"),
            decision=DecisionOutcome.APPROVED,
            reason=None,
            criteria_snapshot=(),
            recorded_at=_NOW,
        )
        await backend.decision_records.append_with_next_version(
            record_id=NotBlankStr("b"),
            task_id=NotBlankStr(sid("t")),
            approval_id=None,
            executing_agent_id=NotBlankStr("exec"),
            reviewer_agent_id=NotBlankStr("rev"),
            decision=DecisionOutcome.REJECTED,
            reason="nope",
            criteria_snapshot=(),
            recorded_at=_NOW,
        )

        records = await backend.decision_records.list_by_task(NotBlankStr(sid("t")))
        versions = [r.version for r in records]
        assert versions == [1, 2]

    async def test_list_by_agent_executor_role(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_task(backend, "tA")
        await backend.decision_records.append_with_next_version(
            record_id=NotBlankStr("e1"),
            task_id=NotBlankStr(sid("tA")),
            approval_id=None,
            executing_agent_id=NotBlankStr("alice"),
            reviewer_agent_id=NotBlankStr("bob"),
            decision=DecisionOutcome.APPROVED,
            reason=None,
            criteria_snapshot=(),
            recorded_at=_NOW,
        )

        as_exec = await backend.decision_records.list_by_agent(
            NotBlankStr("alice"),
            role="executor",
        )
        as_rev = await backend.decision_records.list_by_agent(
            NotBlankStr("alice"),
            role="reviewer",
        )
        # Positive assertion: bob is recorded as the reviewer on the same
        # row, so the reviewer-role path must return exactly one match.
        # Without this the test only proved empty-results path; a broken
        # role filter that silently returned zero for both roles would
        # also pass.
        bob_as_rev = await backend.decision_records.list_by_agent(
            NotBlankStr("bob"),
            role="reviewer",
        )
        assert len(as_exec) == 1
        assert len(as_rev) == 0
        assert len(bob_as_rev) == 1

    async def test_query_filters_and_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_task(backend, "qt")
        for idx in range(5):
            await backend.decision_records.append_with_next_version(
                record_id=NotBlankStr(f"q{idx}"),
                task_id=NotBlankStr(sid("qt")),
                approval_id=None,
                executing_agent_id=NotBlankStr("ex"),
                reviewer_agent_id=NotBlankStr("rv"),
                decision=DecisionOutcome.APPROVED,
                reason=None,
                criteria_snapshot=(),
                recorded_at=_NOW,
            )
        by_task = await backend.decision_records.query(
            DecisionFilterSpec(task_id=NotBlankStr(sid("qt"))),
        )
        assert len(by_task) == 5
        page = await backend.decision_records.query(
            DecisionFilterSpec(task_id=NotBlankStr(sid("qt"))),
            limit=2,
            offset=2,
        )
        assert len(page) == 2
        by_agent = await backend.decision_records.query(
            DecisionFilterSpec(agent_id=NotBlankStr("ex"), role="executor"),
        )
        assert len(by_agent) == 5
        none_match = await backend.decision_records.query(
            DecisionFilterSpec(task_id=NotBlankStr("ghost")),
        )
        assert none_match == ()

    async def test_query_rejects_invalid_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.decision_records.query(DecisionFilterSpec(), limit=0)
        with pytest.raises(QueryError):
            await backend.decision_records.query(DecisionFilterSpec(), offset=-1)

    async def test_append_generic_surface(self, backend: PersistenceBackend) -> None:
        await _seed_task(backend, "ap")
        event = DecisionRecord(
            id=NotBlankStr("ap1"),
            task_id=NotBlankStr(sid("ap")),
            approval_id=None,
            executing_agent_id=NotBlankStr("ex"),
            reviewer_agent_id=NotBlankStr("rv"),
            decision=DecisionOutcome.APPROVED,
            reason=None,
            criteria_snapshot=(),
            recorded_at=_NOW,
            version=1,
        )
        await backend.decision_records.append(event)
        rows = await backend.decision_records.list_by_task(NotBlankStr(sid("ap")))
        assert len(rows) == 1
        assert rows[0].task_id == sid("ap")

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        await _seed_task(backend, "pt")
        old = datetime(2020, 1, 1, tzinfo=UTC)
        await backend.decision_records.append_with_next_version(
            record_id=NotBlankStr("old1"),
            task_id=NotBlankStr(sid("pt")),
            approval_id=None,
            executing_agent_id=NotBlankStr("ex"),
            reviewer_agent_id=NotBlankStr("rv"),
            decision=DecisionOutcome.APPROVED,
            reason=None,
            criteria_snapshot=(),
            recorded_at=old,
        )
        await backend.decision_records.append_with_next_version(
            record_id=NotBlankStr("new1"),
            task_id=NotBlankStr(sid("pt")),
            approval_id=None,
            executing_agent_id=NotBlankStr("ex"),
            reviewer_agent_id=NotBlankStr("rv"),
            decision=DecisionOutcome.APPROVED,
            reason=None,
            criteria_snapshot=(),
            recorded_at=_NOW,
        )
        removed = await backend.decision_records.purge_before(
            datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert removed == 1
        remaining = await backend.decision_records.list_by_task(
            NotBlankStr(sid("pt")),
        )
        assert len(remaining) == 1
        assert remaining[0].id == "new1"

    async def test_purge_before_rejects_naive(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises((ValueError, QueryError)):
            await backend.decision_records.purge_before(
                datetime(2025, 1, 1),  # noqa: DTZ001 -- naive on purpose
            )
