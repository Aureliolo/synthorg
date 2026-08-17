"""Parametrized conformance tests for Task, CostRecord, and Message."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from synthorg.budget.call_category import LLMCallCategory
from synthorg.communication.message import FilePart, Message, TextPart
from synthorg.core.persistence_errors import QueryError
from synthorg.core.task_enums import BlockedReason, TaskSource, TaskStatus
from synthorg.persistence.message_protocol import MessageFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import as_uuid, sid
from tests.unit.persistence.conftest import make_message, make_task


@pytest.mark.integration
class TestTaskRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        task = make_task(task_id="t1", title="First task")
        await backend.tasks.save(task)
        fetched = await backend.tasks.get(sid("t1"))
        assert fetched is not None
        assert fetched.id == as_uuid("t1")
        assert fetched.title == "First task"

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.tasks.get("missing") is None

    async def test_requested_by_user_id_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        task = make_task(task_id="t-owner", requested_by_user_id="user-42")
        await backend.tasks.save(task)
        fetched = await backend.tasks.get(sid("t-owner"))
        assert fetched is not None
        assert fetched.requested_by_user_id == "user-42"

    async def test_requested_by_user_id_defaults_none(
        self, backend: PersistenceBackend
    ) -> None:
        task = make_task(task_id="t-no-owner")
        await backend.tasks.save(task)
        fetched = await backend.tasks.get(sid("t-no-owner"))
        assert fetched is not None
        assert fetched.requested_by_user_id is None

    async def test_created_at_round_trips_to_the_exact_instant(
        self, backend: PersistenceBackend
    ) -> None:
        """The row is the only durable answer to "how long has this run".

        Both duration reporting and the cockpit's stuck heuristic measure
        against this value, so an approximate round trip is a wrong answer
        rather than a rounded one.
        """
        filed = datetime(2026, 4, 29, 8, 15, 30, 123456, tzinfo=UTC)
        task = make_task(task_id="t-filed").model_copy(update={"created_at": filed})
        await backend.tasks.save(task)
        fetched = await backend.tasks.get(sid("t-filed"))
        assert fetched is not None
        assert fetched.created_at == filed

    async def test_an_upsert_does_not_re_date_an_existing_task(
        self, backend: PersistenceBackend
    ) -> None:
        """A later write must not reset when the task was filed.

        Every status hop upserts the whole row, and a caller that rebuilt the
        task rather than loading it carries a freshly-defaulted timestamp, so
        the column stays out of the update set.
        """
        filed = datetime(2026, 4, 29, 8, 15, 30, tzinfo=UTC)
        await backend.tasks.save(
            make_task(task_id="t-refiled").model_copy(update={"created_at": filed})
        )
        await backend.tasks.save(
            make_task(
                task_id="t-refiled",
                title="Same task, later write",
            ).model_copy(update={"created_at": filed + timedelta(hours=3)})
        )
        fetched = await backend.tasks.get(sid("t-refiled"))
        assert fetched is not None
        assert fetched.title == "Same task, later write"
        assert fetched.created_at == filed

    async def test_budget_and_provenance_fields_round_trip(
        self, backend: PersistenceBackend
    ) -> None:
        forecast_id = uuid4()
        task = make_task(task_id="t-budget").model_copy(
            update={
                "hard_ceiling": 12.5,
                # Both ceilings, because the in-loop checker resolves both
                # and a backend that carried one would leave a flat-rate run
                # unbounded on resume while everything else looked right.
                "hard_token_ceiling": 250_000,
                "forecast_id": forecast_id,
                "source": TaskSource.CLIENT,
                "middleware_override": ("retry", "budget_guard"),
                "metadata": {"label": "vip", "wave": 3},
            }
        )
        await backend.tasks.save(task)
        fetched = await backend.tasks.get(sid("t-budget"))
        assert fetched is not None
        assert fetched.hard_ceiling == 12.5
        assert fetched.hard_token_ceiling == 250_000
        assert fetched.forecast_id == forecast_id
        assert fetched.source is TaskSource.CLIENT
        assert fetched.middleware_override == ("retry", "budget_guard")
        assert fetched.metadata == {"label": "vip", "wave": 3}

        # The update arm of the upsert, not just the insert: an operator
        # raising a ceiling to resume a parked run writes through this path,
        # and a column left out of the DO UPDATE list persists the first
        # value forever while the API reports the new one.
        await backend.tasks.save(
            task.model_copy(
                update={"hard_ceiling": 25.0, "hard_token_ceiling": 500_000}
            )
        )
        refetched = await backend.tasks.get(sid("t-budget"))
        assert refetched is not None
        assert refetched.hard_ceiling == 25.0
        assert refetched.hard_token_ceiling == 500_000

    async def test_budget_and_provenance_fields_default(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.tasks.save(make_task(task_id="t-budget-default"))
        fetched = await backend.tasks.get(sid("t-budget-default"))
        assert fetched is not None
        assert fetched.hard_ceiling is None
        assert fetched.hard_token_ceiling is None
        assert fetched.forecast_id is None
        assert fetched.source is None
        assert fetched.middleware_override is None
        assert fetched.metadata == {}

    async def test_upsert_updates_existing(self, backend: PersistenceBackend) -> None:
        task = make_task(task_id="t2", title="Original")
        await backend.tasks.save(task)
        updated = task.model_copy(update={"title": "Updated"})
        await backend.tasks.save(updated)
        fetched = await backend.tasks.get(sid("t2"))
        assert fetched is not None
        assert fetched.title == "Updated"

    async def test_save_many_inserts_and_upserts(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.tasks.save(make_task(task_id="t1", title="Original"))
        await backend.tasks.save_many(
            (
                make_task(task_id="t1", title="Replaced"),
                make_task(task_id="t2", title="Fresh"),
            ),
        )
        first = await backend.tasks.get(sid("t1"))
        second = await backend.tasks.get(sid("t2"))
        assert first is not None
        assert first.title == "Replaced"
        assert second is not None
        assert second.title == "Fresh"

    async def test_save_many_empty_is_noop(self, backend: PersistenceBackend) -> None:
        await backend.tasks.save_many(())
        assert await backend.tasks.list_items() == ()

    async def test_list_all(self, backend: PersistenceBackend) -> None:

        await backend.tasks.save(make_task(task_id="t1"))
        await backend.tasks.save(make_task(task_id="t2"))
        tasks = await backend.tasks.list_items()
        assert len(tasks) == 2

    async def test_list_items_in_id_order(self, backend: PersistenceBackend) -> None:

        await backend.tasks.save(make_task(task_id="t3"))
        await backend.tasks.save(make_task(task_id="t1"))
        await backend.tasks.save(make_task(task_id="t2"))
        tasks = await backend.tasks.list_items()
        assert len(tasks) == 3
        assert [t.id for t in tasks] == sorted(
            [as_uuid("t1"), as_uuid("t2"), as_uuid("t3")], key=str
        )

    async def test_list_filter_by_project(self, backend: PersistenceBackend) -> None:
        from synthorg.persistence.task_protocol import TaskFilterSpec

        await backend.tasks.save(make_task(task_id="t1", project="proj_a"))
        await backend.tasks.save(make_task(task_id="t2", project="proj_b"))
        tasks = await backend.tasks.query(TaskFilterSpec(project="proj_a"))
        assert len(tasks) == 1
        assert tasks[0].id == as_uuid("t1")

    async def test_list_filter_by_plan(self, backend: PersistenceBackend) -> None:
        """The reverse plan-to-tasks lookup the rollup depends on."""
        from synthorg.persistence.task_protocol import TaskFilterSpec

        # The linkage is a both-or-neither pair, so a dispatched task always
        # names the item it implements; t3 is a directly filed task.
        await backend.tasks.save(
            make_task(
                task_id="t1",
                plan_id=as_uuid("plan-a"),
                plan_item_id=as_uuid("item-a"),
            )
        )
        await backend.tasks.save(
            make_task(
                task_id="t2",
                plan_id=as_uuid("plan-b"),
                plan_item_id=as_uuid("item-b"),
            )
        )
        await backend.tasks.save(make_task(task_id="t3", plan_id=None))
        tasks = await backend.tasks.query(TaskFilterSpec(plan=as_uuid("plan-a")))
        assert len(tasks) == 1
        assert tasks[0].id == as_uuid("t1")

    async def test_plan_linkage_roundtrips(self, backend: PersistenceBackend) -> None:
        await backend.tasks.save(
            make_task(
                task_id="t-linked",
                plan_id=as_uuid("plan-a"),
                plan_item_id=as_uuid("item-a"),
            )
        )
        fetched = await backend.tasks.get(sid("t-linked"))
        assert fetched is not None
        assert fetched.plan_id == as_uuid("plan-a")
        assert fetched.plan_item_id == as_uuid("item-a")

    async def test_list_filter_by_status(self, backend: PersistenceBackend) -> None:
        from synthorg.persistence.task_protocol import TaskFilterSpec

        await backend.tasks.save(make_task(task_id="t1", status=TaskStatus.CREATED))
        await backend.tasks.save(make_task(task_id="t2", status=TaskStatus.IN_PROGRESS))
        tasks = await backend.tasks.query(TaskFilterSpec(status=TaskStatus.CREATED))
        assert len(tasks) == 1
        assert tasks[0].id == as_uuid("t1")

    async def test_list_filter_by_blocked_reason(
        self, backend: PersistenceBackend
    ) -> None:
        """The whole review-staffing heal turns on this filter.

        BLOCKED is reached from several directions that mean different
        things, and the sweep must see only the parks that wait on staffing:
        an escalation waits on a human's answer and must never be re-judged.
        Both backends therefore have to narrow on the column, not just on the
        status beside it.
        """
        from synthorg.persistence.task_protocol import TaskFilterSpec

        for task_id, reason in (
            ("t-reviewer", BlockedReason.REVIEWER_UNSTAFFED),
            ("t-red-team", BlockedReason.RED_TEAM_UNSTAFFED),
            ("t-escalated", BlockedReason.ORACLE_ESCALATED),
        ):
            await backend.tasks.save(
                make_task(task_id=task_id, status=TaskStatus.BLOCKED).model_copy(
                    update={"blocked_reason": reason}
                )
            )

        parked = await backend.tasks.query(
            TaskFilterSpec(
                status=TaskStatus.BLOCKED,
                blocked_reason=BlockedReason.REVIEWER_UNSTAFFED,
            )
        )

        assert [t.id for t in parked] == [as_uuid("t-reviewer")]

    @pytest.mark.parametrize("reason", list(BlockedReason))
    async def test_blocked_reason_round_trips(
        self, backend: PersistenceBackend, reason: BlockedReason
    ) -> None:
        """A park that cannot say why it is parked is a park nothing releases.

        Parametrised over every member rather than a representative one, because
        a representative one is exactly what let ``NO_CAPABLE_AGENT`` ship
        unwritable: the enum, the DTO and three writers all carried it while both
        backends' CHECK constraints listed four values, so every one of those
        parks raised on insert and the task never reached BLOCKED at all.
        """
        await backend.tasks.save(
            make_task(task_id="t-parked", status=TaskStatus.BLOCKED).model_copy(
                update={"blocked_reason": reason}
            )
        )

        fetched = await backend.tasks.get(sid("t-parked"))

        assert fetched is not None
        assert fetched.blocked_reason is reason

    async def test_delete_returns_true(self, backend: PersistenceBackend) -> None:
        await backend.tasks.save(make_task(task_id="t1"))
        assert await backend.tasks.delete(sid("t1")) is True
        assert await backend.tasks.get(sid("t1")) is None

    async def test_delete_returns_false_when_missing(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.tasks.delete("missing") is False


@pytest.mark.integration
class TestCostRecordRepository:
    async def test_save_and_query(self, backend: PersistenceBackend) -> None:
        from synthorg.budget.cost_record import CostRecord
        from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec

        task = make_task(task_id="t1")
        await backend.tasks.save(task)

        record = CostRecord(
            agent_id="agent_1",
            task_id=sid("t1"),
            provider="test-provider",
            model="test-basic-001",
            input_tokens=100,
            output_tokens=50,
            cost=0.05,
            currency="EUR",
            timestamp=datetime(2026, 4, 10, 12, tzinfo=UTC),
            call_category=LLMCallCategory.PRODUCTIVE,
        )
        await backend.cost_records.append(record)

        results = await backend.cost_records.query(
            CostRecordFilterSpec(agent_id="agent_1")
        )
        assert len(results) == 1
        assert results[0].cost == 0.05

    async def test_query_pagination(self, backend: PersistenceBackend) -> None:
        from synthorg.budget.cost_record import CostRecord
        from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec

        task = make_task(task_id="t_query_pag")
        await backend.tasks.save(task)

        base = datetime(2026, 4, 10, 12, tzinfo=UTC)
        for i in range(4):
            await backend.cost_records.append(
                CostRecord(
                    agent_id="agent_pg",
                    task_id=sid("t_query_pag"),
                    provider="test-provider",
                    model="test-basic-001",
                    input_tokens=10,
                    output_tokens=5,
                    cost=0.01 * (i + 1),
                    currency="EUR",
                    timestamp=base + timedelta(seconds=i),
                    call_category=LLMCallCategory.PRODUCTIVE,
                ),
            )

        page = await backend.cost_records.query(
            CostRecordFilterSpec(agent_id="agent_pg"),
            limit=2,
            offset=1,
        )

        # ORDER BY timestamp DESC: rows are i=3, i=2, i=1, i=0;
        # offset=1 limit=2 -> i=2, i=1 (costs 0.03, 0.02).
        assert len(page) == 2
        assert [round(r.cost, 2) for r in page] == [0.03, 0.02]

    async def test_aggregate_sum(self, backend: PersistenceBackend) -> None:
        from synthorg.budget.cost_record import CostRecord

        task = make_task(task_id="t1")
        await backend.tasks.save(task)

        for cost in (0.1, 0.2, 0.3):
            await backend.cost_records.append(
                CostRecord(
                    agent_id="agent_1",
                    task_id=sid("t1"),
                    provider="test-provider",
                    model="test-basic-001",
                    input_tokens=10,
                    output_tokens=10,
                    cost=cost,
                    currency="USD",
                    timestamp=datetime(2026, 4, 10, 12, tzinfo=UTC),
                    call_category=LLMCallCategory.PRODUCTIVE,
                )
            )

        total = await backend.cost_records.aggregate(agent_id="agent_1")
        assert total == pytest.approx(0.6)

    async def test_aggregate_empty_returns_zero(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.cost_records.aggregate(agent_id="agent_1") == 0.0

    async def test_aggregate_rejects_mixed_currency(
        self, backend: PersistenceBackend
    ) -> None:
        from synthorg.budget.cost_record import CostRecord
        from synthorg.budget.errors import MixedCurrencyAggregationError

        task = make_task(task_id="t-mixed")
        await backend.tasks.save(task)

        for currency, cost in (("USD", 0.1), ("EUR", 0.2)):
            await backend.cost_records.append(
                CostRecord(
                    agent_id="agent_mix",
                    task_id=sid("t-mixed"),
                    provider="test-provider",
                    model="test-basic-001",
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

    async def test_prompt_class_id_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        from synthorg.budget.cost_record import CostRecord
        from synthorg.llm.prompt_purpose import PromptPurposeId
        from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec

        task = make_task(task_id="t-pclass")
        await backend.tasks.save(task)

        record = CostRecord(
            agent_id="agent_pclass",
            task_id=sid("t-pclass"),
            provider="test-provider",
            model="test-basic-001",
            input_tokens=100,
            output_tokens=50,
            cost=0.05,
            currency="USD",
            timestamp=datetime(2026, 4, 10, 12, tzinfo=UTC),
            call_category=LLMCallCategory.PRODUCTIVE,
            prompt_class_id=PromptPurposeId.MEMORY_RERANK,
        )
        await backend.cost_records.append(record)

        results = await backend.cost_records.query(
            CostRecordFilterSpec(agent_id="agent_pclass")
        )
        assert len(results) == 1
        assert results[0].prompt_class_id == PromptPurposeId.MEMORY_RERANK

    async def test_prompt_class_id_defaults_none(
        self, backend: PersistenceBackend
    ) -> None:
        from synthorg.budget.cost_record import CostRecord
        from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec

        task = make_task(task_id="t-pclass-none")
        await backend.tasks.save(task)

        record = CostRecord(
            agent_id="agent_pclass_none",
            task_id=sid("t-pclass-none"),
            provider="test-provider",
            model="test-basic-001",
            input_tokens=10,
            output_tokens=5,
            cost=0.01,
            currency="USD",
            timestamp=datetime(2026, 4, 10, 12, tzinfo=UTC),
            call_category=LLMCallCategory.PRODUCTIVE,
        )
        await backend.cost_records.append(record)

        results = await backend.cost_records.query(
            CostRecordFilterSpec(agent_id="agent_pclass_none")
        )
        assert len(results) == 1
        assert results[0].prompt_class_id is None

    async def test_query_filters_by_prompt_class_id(
        self, backend: PersistenceBackend
    ) -> None:
        from synthorg.budget.cost_record import CostRecord
        from synthorg.llm.prompt_purpose import PromptPurposeId
        from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec

        task = make_task(task_id="t-pclass-filter")
        await backend.tasks.save(task)

        def _record(purpose: PromptPurposeId | None) -> CostRecord:
            return CostRecord(
                agent_id="agent_pclass_filter",
                task_id=sid("t-pclass-filter"),
                provider="test-provider",
                model="test-basic-001",
                input_tokens=10,
                output_tokens=5,
                cost=0.01,
                currency="USD",
                timestamp=datetime(2026, 4, 10, 12, tzinfo=UTC),
                call_category=LLMCallCategory.PRODUCTIVE,
                prompt_class_id=purpose,
            )

        await backend.cost_records.append(_record(PromptPurposeId.MEMORY_RERANK))
        await backend.cost_records.append(_record(PromptPurposeId.COS_CHAT))
        await backend.cost_records.append(_record(None))

        results = await backend.cost_records.query(
            CostRecordFilterSpec(prompt_class_id=PromptPurposeId.MEMORY_RERANK)
        )
        assert len(results) == 1
        assert results[0].prompt_class_id == PromptPurposeId.MEMORY_RERANK


@pytest.mark.integration
class TestMessageRepository:
    async def test_append_and_get_history(self, backend: PersistenceBackend) -> None:
        msg = make_message(msg_id=uuid4(), channel="chan1", content="hello")
        await backend.messages.append(msg)
        history = await backend.messages.get_history("chan1")
        assert len(history) == 1

    async def test_get_history_newest_first(self, backend: PersistenceBackend) -> None:
        for i in range(3):
            await backend.messages.append(
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
            await backend.messages.append(
                make_message(
                    msg_id=uuid4(),
                    channel="chan1",
                    timestamp=datetime(2026, 4, 10, 12, i, tzinfo=UTC),
                )
            )
        history = await backend.messages.get_history("chan1", limit=2)
        assert len(history) == 2

    async def test_get_history_offset_pages_deeper(
        self, backend: PersistenceBackend
    ) -> None:
        for i in range(5):
            await backend.messages.append(
                make_message(
                    msg_id=uuid4(),
                    channel="chan1",
                    content=f"m{i}",
                    timestamp=datetime(2026, 4, 10, 12, i, tzinfo=UTC),
                )
            )
        first = await backend.messages.get_history("chan1", limit=2, offset=0)
        second = await backend.messages.get_history("chan1", limit=2, offset=2)
        third = await backend.messages.get_history("chan1", limit=2, offset=4)
        # Newest-first, so offsets walk from m4 down to m0 without overlap.
        assert [m.text for m in first] == ["m4", "m3"]
        assert [m.text for m in second] == ["m2", "m1"]
        assert [m.text for m in third] == ["m0"]

    async def test_get_history_filters_by_channel(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.messages.append(make_message(msg_id=uuid4(), channel="chan1"))
        await backend.messages.append(make_message(msg_id=uuid4(), channel="chan2"))
        assert len(await backend.messages.get_history("chan1")) == 1
        assert len(await backend.messages.get_history("chan2")) == 1

    async def test_get_by_id_returns_matching_message(
        self, backend: PersistenceBackend
    ) -> None:
        msg_id = uuid4()
        await backend.messages.append(
            make_message(msg_id=msg_id, channel="chan1", content="needle")
        )
        await backend.messages.append(
            make_message(msg_id=uuid4(), channel="chan1", content="haystack")
        )
        found = await backend.messages.get_by_id("chan1", str(msg_id))
        assert found is not None
        assert str(found.id) == str(msg_id)
        assert found.channel == "chan1"

    async def test_get_by_id_unknown_id_returns_none(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.messages.append(make_message(msg_id=uuid4(), channel="chan1"))
        assert await backend.messages.get_by_id("chan1", str(uuid4())) is None

    async def test_get_by_id_wrong_channel_returns_none(
        self, backend: PersistenceBackend
    ) -> None:
        msg_id = uuid4()
        await backend.messages.append(make_message(msg_id=msg_id, channel="chan1"))
        # The id exists but on a different channel: the channel scoping
        # predicate must reject the cross-channel read.
        assert await backend.messages.get_by_id("chan2", str(msg_id)) is None

    async def test_delete_removes_row_and_returns_true(
        self, backend: PersistenceBackend
    ) -> None:
        msg_id = uuid4()
        await backend.messages.append(
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
        await backend.messages.append(
            make_message(msg_id=msg_id, channel="chan1"),
        )

        first = await backend.messages.delete(str(msg_id))
        second = await backend.messages.delete(str(msg_id))
        assert first is True
        assert second is False

    async def test_delete_concurrent_invocations_only_one_succeeds(
        self, backend: PersistenceBackend
    ) -> None:
        """Concurrent deletes of the same id race safely.

        Two async tasks issue DELETE for the same row; exactly one
        must report ``True`` and the other ``False``. Guards against
        repos that miscount affected rows when the underlying driver
        serialises inside a connection pool.
        """
        import asyncio

        msg_id = uuid4()
        await backend.messages.append(
            make_message(msg_id=msg_id, channel="chan1"),
        )

        results = await asyncio.gather(
            backend.messages.delete(str(msg_id)),
            backend.messages.delete(str(msg_id)),
        )

        assert sum(1 for r in results if r) == 1
        assert sum(1 for r in results if not r) == 1
        assert len(await backend.messages.get_history("chan1")) == 0

    async def test_attachments_round_trip(self, backend: PersistenceBackend) -> None:
        """Non-empty attachments survive a persist + read cycle.

        Exercises the attachments serialise/deserialise path on both
        backends (SQLite TEXT-JSON column, Postgres JSONB).
        """
        msg_id = uuid4()
        original = Message.model_validate(
            {
                "id": msg_id,
                "from": "alice",
                "to": "bob",
                "channel": "att",
                "parts": (TextPart(text="see attached"),),
                "attachments": (
                    FilePart(uri="file:///tmp/report.pdf"),
                    FilePart(uri="file:///tmp/data.csv"),
                ),
                "type": make_message().type,
                "priority": make_message().priority,
                "timestamp": datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            }
        )
        await backend.messages.append(original)
        history = await backend.messages.get_history("att")
        assert len(history) == 1
        restored = history[0]
        assert len(restored.attachments) == 2
        uris = {p.uri for p in restored.attachments if isinstance(p, FilePart)}
        assert uris == {"file:///tmp/report.pdf", "file:///tmp/data.csv"}

    async def test_empty_attachments_default(self, backend: PersistenceBackend) -> None:
        msg_id = uuid4()
        await backend.messages.append(
            make_message(msg_id=msg_id, channel="noatt"),
        )
        history = await backend.messages.get_history("noatt")
        assert history[0].attachments == ()

    async def test_query_by_channel_and_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        for _ in range(4):
            await backend.messages.append(
                make_message(msg_id=uuid4(), channel="qc"),
            )
        await backend.messages.append(
            make_message(msg_id=uuid4(), channel="other"),
        )
        all_qc = await backend.messages.query(
            MessageFilterSpec(channel="qc"),
        )
        assert len(all_qc) == 4
        page = await backend.messages.query(
            MessageFilterSpec(channel="qc"), limit=2, offset=2
        )
        assert len(page) == 2

    async def test_query_rejects_invalid_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.messages.query(MessageFilterSpec(), limit=0)
        with pytest.raises(QueryError):
            await backend.messages.query(MessageFilterSpec(), offset=-1)

    async def test_purge_before(self, backend: PersistenceBackend) -> None:
        old = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
        new = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        await backend.messages.append(
            make_message(msg_id=uuid4(), channel="pp", timestamp=old),
        )
        await backend.messages.append(
            make_message(msg_id=uuid4(), channel="pp", timestamp=new),
        )
        removed = await backend.messages.purge_before(
            datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert removed == 1
        remaining = await backend.messages.get_history("pp")
        assert len(remaining) == 1
        assert remaining[0].timestamp == new


@pytest.mark.integration
class TestCostRecordPurge:
    async def test_purge_before_removes_old_rows(
        self, backend: PersistenceBackend
    ) -> None:
        from synthorg.budget.cost_record import CostRecord

        task = make_task(task_id="cprg")
        await backend.tasks.save(task)
        old = datetime(2020, 1, 1, 12, 0, tzinfo=UTC)
        new = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        for ts, agent in ((old, "a-old"), (new, "a-new")):
            await backend.cost_records.append(
                CostRecord(
                    agent_id=agent,
                    task_id=sid("cprg"),
                    provider="test-provider",
                    model="test-basic-001",
                    input_tokens=10,
                    output_tokens=5,
                    cost=0.01,
                    currency="EUR",
                    timestamp=ts,
                    call_category=LLMCallCategory.PRODUCTIVE,
                )
            )
        removed = await backend.cost_records.purge_before(
            datetime(2025, 1, 1, tzinfo=UTC),
        )
        assert removed == 1
