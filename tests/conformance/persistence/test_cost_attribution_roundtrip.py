"""Both-backend round-trip: persisted prompt_class_id drives the dashboard DTO.

Persistence round-trips preserve ``prompt_class_id`` as a bare string; this
crosses the persistence -> analytics boundary: it persists attributed cost on
each backend, reads it back, and feeds it through
``CallAnalyticsService.get_prompt_class_breakdown`` to assert the by-purpose
dashboard rows reconstruct correctly. It also pins the fact that latency /
cache / success are in-memory telemetry the ``cost_records`` schema does not
persist, so they come back ``None``.
"""

from datetime import UTC, datetime

import pytest

from synthorg.budget.call_analytics import CallAnalyticsService
from synthorg.budget.call_analytics_config import CallAnalyticsConfig
from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.core.persistence_errors import QueryError
from synthorg.llm.model_capability_policy import capability_for_purpose
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.persistence.cost_record_protocol import CostRecordFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import sid
from tests.unit.persistence.conftest import make_task


@pytest.mark.integration
class TestCostAttributionRoundTrip:
    async def test_persisted_attribution_drives_breakdown(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.tasks.save(make_task(task_id="t-attr"))
        timestamp = datetime(2026, 5, 1, 12, tzinfo=UTC)

        def _record(purpose: PromptPurposeId | None, cost: float) -> CostRecord:
            return CostRecord(
                agent_id="agent-attr",
                task_id=sid("t-attr"),
                provider="test-provider",
                model="example-basic-001",
                input_tokens=100,
                output_tokens=50,
                cost=cost,
                currency="EUR",
                timestamp=timestamp,
                call_category=LLMCallCategory.PRODUCTIVE,
                prompt_class_id=purpose,
            )

        await backend.cost_records.append(_record(PromptPurposeId.MEMORY_RERANK, 0.05))
        await backend.cost_records.append(_record(PromptPurposeId.COS_CHAT, 0.03))
        await backend.cost_records.append(_record(None, 0.10))

        persisted = await backend.cost_records.query(
            CostRecordFilterSpec(agent_id="agent-attr")
        )
        assert len(persisted) == 3

        # The dashboard read path aggregates the in-memory tracker; seed it from
        # what each backend persisted to prove persisted attribution reconstructs
        # into the by-purpose breakdown.
        tracker = CostTracker()
        for record in persisted:
            await tracker.record(record)
        service = CallAnalyticsService(
            cost_tracker=tracker, config=CallAnalyticsConfig()
        )
        breakdown = await service.get_prompt_class_breakdown()

        ids = [row.prompt_class_id for row in breakdown.rows]
        # The unattributed record keeps its own row so the breakdown still sums
        # to the headline total; it sorts ahead of every registered id.
        assert ids == [
            None,
            PromptPurposeId.COS_CHAT,
            PromptPurposeId.MEMORY_RERANK,
        ]
        unattributed = breakdown.rows[0]
        assert unattributed.total_cost == pytest.approx(0.10)
        assert unattributed.call_count == 1

        by_id = {row.prompt_class_id: row for row in breakdown.rows}
        rerank = by_id[PromptPurposeId.MEMORY_RERANK]
        assert rerank.capability == capability_for_purpose(
            PromptPurposeId.MEMORY_RERANK
        )
        assert rerank.total_cost == pytest.approx(0.05)
        assert rerank.currency == "EUR"
        assert rerank.call_count == 1
        assert rerank.input_tokens == 100
        assert rerank.output_tokens == 50
        # Latency / success are not columns on cost_records, so a repo
        # round-trip drops them and the breakdown reports them absent. The
        # cached counts ARE columns, so the share is a real zero over the
        # persisted input tokens rather than an absence.
        assert rerank.avg_latency_ms is None
        assert rerank.cached_input_share == pytest.approx(0.0)
        assert rerank.success_rate is None

    async def test_subsystem_spend_persists_with_no_owner(
        self, backend: PersistenceBackend
    ) -> None:
        """Work owned by no agent and no task still records its spend.

        ``cost_records.task_id`` is a foreign key into ``tasks``. Subsystem
        calls (embedding, reranking, consolidation, safety classification)
        belong to no task, and naming one anyway made every such insert fail
        the constraint, so their spend was silently dropped and the budget
        under-reported. They must persist unowned, carrying their purpose.
        """
        record = CostRecord(
            provider="test-provider",
            model="example-basic-001",
            input_tokens=100,
            output_tokens=0,
            cost=0.02,
            currency="EUR",
            timestamp=datetime(2026, 5, 1, 12, tzinfo=UTC),
            call_category=LLMCallCategory.EMBEDDING,
            prompt_class_id=PromptPurposeId.MEMORY_RERANK,
        )
        assert record.agent_id is None
        assert record.task_id is None

        await backend.cost_records.append(record)

        persisted = await backend.cost_records.query(CostRecordFilterSpec())
        # Both ownership columns, not just the task: a backend that invented
        # an agent_id would still satisfy a task-only filter, which is the
        # exact fabrication this test exists to rule out.
        unowned = [r for r in persisted if r.agent_id is None and r.task_id is None]
        assert len(unowned) == 1
        assert unowned[0].cost == pytest.approx(0.02)
        # The owner is gone but what the call was for is not: that is the
        # whole reason the synthetic task id was never needed.
        assert unowned[0].prompt_class_id == PromptPurposeId.MEMORY_RERANK

    async def test_a_redelivered_record_is_stored_once(
        self, backend: PersistenceBackend
    ) -> None:
        """The claim key is what makes the durable append safe to retry.

        The tracker's in-memory LRU is empty after a restart, so a JetStream
        redelivery reaches the storage layer as a fresh submission; only the
        unique index stops it becoming a second billed row. The unit fake
        enforces no uniqueness, so this is the only place the ``ON CONFLICT``
        clause is exercised at all.
        """
        record = CostRecord(
            provider="test-provider",
            model="example-basic-001",
            input_tokens=10,
            output_tokens=5,
            cost=0.07,
            currency="EUR",
            timestamp=datetime(2026, 5, 1, 12, tzinfo=UTC),
        )

        await backend.cost_records.append(record)
        await backend.cost_records.append(record)

        persisted = await backend.cost_records.query(CostRecordFilterSpec())
        same_claim = [r for r in persisted if r.claim_id == record.claim_id]
        assert len(same_claim) == 1
        assert await backend.cost_records.aggregate() == pytest.approx(0.07)

    async def test_purge_before_rejects_a_naive_threshold(
        self, backend: PersistenceBackend
    ) -> None:
        """One protocol call must not delete a different window per backend.

        ``normalize_utc`` tags a naive value as UTC, so a caller in another
        zone silently purges the wrong retention window on whichever backend
        does not refuse it.
        """
        with pytest.raises(QueryError):
            await backend.cost_records.purge_before(datetime(2026, 5, 1, 12))  # noqa: DTZ001
