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
from synthorg.llm.model_tier_policy import tier_for_purpose
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
                model="example-small-001",
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
        # The unattributed record is excluded; rows sort by prompt_class_id value.
        assert ids == [PromptPurposeId.COS_CHAT, PromptPurposeId.MEMORY_RERANK]

        by_id = {row.prompt_class_id: row for row in breakdown.rows}
        rerank = by_id[PromptPurposeId.MEMORY_RERANK]
        assert rerank.tier == tier_for_purpose(PromptPurposeId.MEMORY_RERANK)
        assert rerank.total_cost == pytest.approx(0.05)
        assert rerank.currency == "EUR"
        assert rerank.call_count == 1
        assert rerank.input_tokens == 100
        assert rerank.output_tokens == 50
        # Latency / cache / success are not columns on cost_records, so a repo
        # round-trip drops them and the breakdown reports them absent.
        assert rerank.avg_latency_ms is None
        assert rerank.cache_hit_rate is None
        assert rerank.success_rate is None
