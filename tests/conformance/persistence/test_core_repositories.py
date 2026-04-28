"""Parametrized conformance tests for Task, CostRecord, and Message."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.core.enums import TaskStatus
from synthorg.persistence.protocol import PersistenceBackend
from tests.unit.persistence.conftest import make_message, make_task


@pytest.mark.integration
class TestTaskRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        task = make_task(task_id="t1", title="First task")
        await backend.tasks.save(task)
        fetched = await backend.tasks.get("t1")
        assert fetched is not None
        assert fetched.id == "t1"
        assert fetched.title == "First task"

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.tasks.get("missing") is None

    async def test_upsert_updates_existing(self, backend: PersistenceBackend) -> None:
        task = make_task(task_id="t2", title="Original")
        await backend.tasks.save(task)
        updated = task.model_copy(update={"title": "Updated"})
        await backend.tasks.save(updated)
        fetched = await backend.tasks.get("t2")
        assert fetched is not None
        assert fetched.title == "Updated"

    async def test_list_all(self, backend: PersistenceBackend) -> None:
        await backend.tasks.save(make_task(task_id="t1"))
        await backend.tasks.save(make_task(task_id="t2"))
        tasks = await backend.tasks.list_tasks()
        assert len(tasks) == 2

    async def test_list_filter_by_project(self, backend: PersistenceBackend) -> None:
        await backend.tasks.save(make_task(task_id="t1", project="proj_a"))
        await backend.tasks.save(make_task(task_id="t2", project="proj_b"))
        tasks = await backend.tasks.list_tasks(project="proj_a")
        assert len(tasks) == 1
        assert tasks[0].id == "t1"

    async def test_list_filter_by_status(self, backend: PersistenceBackend) -> None:
        await backend.tasks.save(make_task(task_id="t1", status=TaskStatus.CREATED))
        await backend.tasks.save(make_task(task_id="t2", status=TaskStatus.IN_PROGRESS))
        tasks = await backend.tasks.list_tasks(status=TaskStatus.CREATED)
        assert len(tasks) == 1
        assert tasks[0].id == "t1"

    async def test_delete_returns_true(self, backend: PersistenceBackend) -> None:
        await backend.tasks.save(make_task(task_id="t1"))
        assert await backend.tasks.delete("t1") is True
        assert await backend.tasks.get("t1") is None

    async def test_delete_returns_false_when_missing(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.tasks.delete("missing") is False


@pytest.mark.integration
class TestCostRecordRepository:
    async def test_save_and_query(self, backend: PersistenceBackend) -> None:
        # Cost records reference tasks; create a task first.
        task = make_task(task_id="t1")
        await backend.tasks.save(task)

        from synthorg.budget.cost_record import CostRecord

        record = CostRecord(
            agent_id="agent_1",
            task_id="t1",
            provider="test-provider",
            model="test-small-001",
            input_tokens=100,
            output_tokens=50,
            cost=0.05,
            currency="EUR",
            timestamp=datetime(2026, 4, 10, 12, tzinfo=UTC),
            call_category=LLMCallCategory.PRODUCTIVE,
        )
        await backend.cost_records.save(record)

        results = await backend.cost_records.query(agent_id="agent_1")
        assert len(results) == 1
        assert results[0].cost == 0.05

    async def test_query_pagination(self, backend: PersistenceBackend) -> None:
        task = make_task(task_id="t_query_pag")
        await backend.tasks.save(task)

        from synthorg.budget.cost_record import CostRecord

        base = datetime(2026, 4, 10, 12, tzinfo=UTC)
        for i in range(4):
            await backend.cost_records.save(
                CostRecord(
                    agent_id="agent_pg",
                    task_id="t_query_pag",
                    provider="test-provider",
                    model="test-small-001",
                    input_tokens=10,
                    output_tokens=5,
                    cost=0.01 * (i + 1),
                    currency="EUR",
                    timestamp=base + timedelta(seconds=i),
                    call_category=LLMCallCategory.PRODUCTIVE,
                ),
            )

        page = await backend.cost_records.query(
            agent_id="agent_pg",
            limit=2,
            offset=1,
        )

        # ORDER BY timestamp DESC: rows are i=3, i=2, i=1, i=0;
        # offset=1 limit=2 -> i=2, i=1 (costs 0.03, 0.02).
        assert len(page) == 2
        assert [round(r.cost, 2) for r in page] == [0.03, 0.02]

    async def test_aggregate_sum(self, backend: PersistenceBackend) -> None:
        task = make_task(task_id="t1")
        await backend.tasks.save(task)

        from synthorg.budget.cost_record import CostRecord

        for cost in (0.1, 0.2, 0.3):
            await backend.cost_records.save(
                CostRecord(
                    agent_id="agent_1",
                    task_id="t1",
                    provider="test-provider",
                    model="test-small-001",
                    input_tokens=10,
                    output_tokens=10,
                    cost=cost,
                    currency="USD",
                    timestamp=datetime(2026, 4, 10, 12, tzinfo=UTC),
                    call_category=LLMCallCategory.PRODUCTIVE,
                )
            )

        total = await backend.cost_records.aggregate(agent_id="agent_1")
        assert abs(total - 0.6) < 1e-9

    async def test_aggregate_empty_returns_zero(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.cost_records.aggregate(agent_id="agent_1") == 0.0

    async def test_aggregate_rejects_mixed_currency(
        self, backend: PersistenceBackend
    ) -> None:
        """The single-query probe must raise, not silently sum USD + EUR."""
        from synthorg.budget.cost_record import CostRecord
        from synthorg.budget.errors import MixedCurrencyAggregationError

        task = make_task(task_id="t-mixed")
        await backend.tasks.save(task)

        for currency, cost in (("USD", 0.1), ("EUR", 0.2)):
            await backend.cost_records.save(
                CostRecord(
                    agent_id="agent_mix",
                    task_id="t-mixed",
                    provider="test-provider",
                    model="test-small-001",
                    input_tokens=10,
                    output_tokens=10,
                    cost=cost,
                    currency=currency,
                    timestamp=datetime(2026, 4, 10, 12, tzinfo=UTC),
                    call_category=LLMCallCategory.PRODUCTIVE,
                )
            )

        with pytest.raises(MixedCurrencyAggregationError) as exc_info:
            await backend.cost_records.aggregate(agent_id="agent_mix")
        assert exc_info.value.currencies == frozenset({"USD", "EUR"})


@pytest.mark.integration
class TestMessageRepository:
    async def test_save_and_get_history(self, backend: PersistenceBackend) -> None:
        msg = make_message(msg_id=uuid4(), channel="chan1", content="hello")
        await backend.messages.save(msg)
        history = await backend.messages.get_history("chan1")
        assert len(history) == 1

    async def test_get_history_newest_first(self, backend: PersistenceBackend) -> None:
        for i in range(3):
            await backend.messages.save(
                make_message(
                    msg_id=uuid4(),
                    channel="chan1",
                    timestamp=datetime(2026, 4, 10, 12, i, tzinfo=UTC),
                    content=f"msg {i}",
                )
            )
        history = await backend.messages.get_history("chan1")
        assert len(history) == 3
        # Newest first
        assert history[0].timestamp > history[1].timestamp
        assert history[1].timestamp > history[2].timestamp

    async def test_get_history_limit(self, backend: PersistenceBackend) -> None:
        for i in range(5):
            await backend.messages.save(
                make_message(
                    msg_id=uuid4(),
                    channel="chan1",
                    timestamp=datetime(2026, 4, 10, 12, i, tzinfo=UTC),
                )
            )
        history = await backend.messages.get_history("chan1", limit=2)
        assert len(history) == 2

    async def test_get_history_filters_by_channel(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.messages.save(make_message(msg_id=uuid4(), channel="chan1"))
        await backend.messages.save(make_message(msg_id=uuid4(), channel="chan2"))
        assert len(await backend.messages.get_history("chan1")) == 1
        assert len(await backend.messages.get_history("chan2")) == 1

    async def test_delete_removes_row_and_returns_true(
        self, backend: PersistenceBackend
    ) -> None:
        msg_id = uuid4()
        await backend.messages.save(
            make_message(msg_id=msg_id, channel="chan1"),
        )
        assert len(await backend.messages.get_history("chan1")) == 1

        deleted = await backend.messages.delete(str(msg_id))
        assert deleted is True
        assert len(await backend.messages.get_history("chan1")) == 0

    async def test_delete_returns_false_when_id_not_found(
        self, backend: PersistenceBackend
    ) -> None:
        deleted = await backend.messages.delete(str(uuid4()))
        assert deleted is False

    async def test_delete_is_idempotent(self, backend: PersistenceBackend) -> None:
        msg_id = uuid4()
        await backend.messages.save(
            make_message(msg_id=msg_id, channel="chan1"),
        )

        first = await backend.messages.delete(str(msg_id))
        second = await backend.messages.delete(str(msg_id))
        assert first is True
        assert second is False
