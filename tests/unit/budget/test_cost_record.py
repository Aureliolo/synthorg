"""Tests for CostRecord model."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.budget.call_category import LLMCallCategory
from synthorg.budget.cost_record import CostRecord

from .conftest import CostRecordFactory

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestCostRecord:
    """Tests for CostRecord validation, immutability, and serialization."""

    def test_valid(self, sample_cost_record: CostRecord) -> None:
        """Verify fixture-provided cost record has expected fields."""
        assert sample_cost_record.agent_id == "sarah_chen"
        assert sample_cost_record.task_id == "task-123"
        assert sample_cost_record.provider == "example-provider"
        assert sample_cost_record.model == "test-model-001"
        assert sample_cost_record.input_tokens == 4500
        assert sample_cost_record.output_tokens == 1200
        assert sample_cost_record.cost_usd == 0.0315

    def test_all_required_fields(self) -> None:
        """Ensure no defaults on required fields — all must be provided."""
        with pytest.raises(ValidationError):
            CostRecord()  # type: ignore[call-arg]

    def test_empty_agent_id_rejected(self) -> None:
        """Reject empty agent_id."""
        with pytest.raises(ValidationError):
            CostRecord(
                agent_id="",
                task_id="task-1",
                provider="test",
                model="test-model",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            )

    def test_whitespace_agent_id_rejected(self) -> None:
        """Reject whitespace-only agent_id."""
        with pytest.raises(ValidationError, match="whitespace-only"):
            CostRecord(
                agent_id="   ",
                task_id="task-1",
                provider="test",
                model="test-model",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            )

    def test_empty_task_id_rejected(self) -> None:
        """Reject empty task_id."""
        with pytest.raises(ValidationError):
            CostRecord(
                agent_id="agent-1",
                task_id="",
                provider="test",
                model="test-model",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            )

    def test_empty_provider_rejected(self) -> None:
        """Reject empty provider."""
        with pytest.raises(ValidationError):
            CostRecord(
                agent_id="agent-1",
                task_id="task-1",
                provider="",
                model="test-model",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            )

    def test_empty_model_rejected(self) -> None:
        """Reject empty model."""
        with pytest.raises(ValidationError):
            CostRecord(
                agent_id="agent-1",
                task_id="task-1",
                provider="test",
                model="",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            )

    def test_negative_input_tokens_rejected(self) -> None:
        """Reject negative input_tokens."""
        with pytest.raises(ValidationError):
            CostRecord(
                agent_id="agent-1",
                task_id="task-1",
                provider="test",
                model="test-model",
                input_tokens=-1,
                output_tokens=50,
                cost_usd=0.01,
                timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            )

    def test_negative_output_tokens_rejected(self) -> None:
        """Reject negative output_tokens."""
        with pytest.raises(ValidationError):
            CostRecord(
                agent_id="agent-1",
                task_id="task-1",
                provider="test",
                model="test-model",
                input_tokens=100,
                output_tokens=-1,
                cost_usd=0.01,
                timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            )

    def test_zero_tokens_accepted(self) -> None:
        """Accept both token counts at zero when cost is zero."""
        record = CostRecord(
            agent_id="agent-1",
            task_id="task-1",
            provider="test",
            model="test-model",
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            timestamp=datetime(2026, 2, 27, tzinfo=UTC),
        )
        assert record.input_tokens == 0
        assert record.output_tokens == 0

    def test_negative_cost_rejected(self) -> None:
        """Reject negative cost_usd."""
        with pytest.raises(ValidationError):
            CostRecord(
                agent_id="agent-1",
                task_id="task-1",
                provider="test",
                model="test-model",
                input_tokens=100,
                output_tokens=50,
                cost_usd=-0.01,
                timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            )

    def test_positive_cost_with_zero_tokens_rejected(self) -> None:
        """Reject positive cost with zero tokens."""
        with pytest.raises(ValidationError, match="both token counts are zero"):
            CostRecord(
                agent_id="agent-1",
                task_id="task-1",
                provider="test",
                model="test-model",
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.01,
                timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            )

    def test_zero_cost_with_tokens_accepted(self) -> None:
        """Accept zero cost with tokens (free tier / test)."""
        record = CostRecord(
            agent_id="agent-1",
            task_id="task-1",
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.0,
            timestamp=datetime(2026, 2, 27, tzinfo=UTC),
        )
        assert record.cost_usd == 0.0
        assert record.input_tokens == 100

    def test_naive_datetime_rejected(self) -> None:
        """Reject naive (timezone-unaware) timestamps."""
        with pytest.raises(ValidationError, match="timestamp"):
            CostRecord(
                agent_id="agent-1",
                task_id="task-1",
                provider="test",
                model="test-model",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.001,
                timestamp=datetime(2026, 2, 27),  # noqa: DTZ001
            )

    def test_frozen(self, sample_cost_record: CostRecord) -> None:
        """Ensure CostRecord is immutable (append-only pattern)."""
        with pytest.raises(ValidationError):
            sample_cost_record.cost_usd = 999.0  # type: ignore[misc]

    def test_json_roundtrip(self, sample_cost_record: CostRecord) -> None:
        """Verify datetime serialization to ISO 8601."""
        json_str = sample_cost_record.model_dump_json()
        restored = CostRecord.model_validate_json(json_str)
        assert restored.agent_id == sample_cost_record.agent_id
        assert restored.timestamp == sample_cost_record.timestamp
        assert restored.cost_usd == sample_cost_record.cost_usd

    def test_call_category_none_default(self) -> None:
        """Default call_category is None."""
        record = CostRecord(
            agent_id="agent-1",
            task_id="task-1",
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            timestamp=datetime(2026, 2, 27, tzinfo=UTC),
        )
        assert record.call_category is None

    def test_call_category_productive(self) -> None:
        """Accept PRODUCTIVE call_category."""
        record = CostRecord(
            agent_id="agent-1",
            task_id="task-1",
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            call_category=LLMCallCategory.PRODUCTIVE,
        )
        assert record.call_category == LLMCallCategory.PRODUCTIVE

    def test_call_category_coordination(self) -> None:
        """Accept COORDINATION call_category."""
        record = CostRecord(
            agent_id="agent-1",
            task_id="task-1",
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            call_category=LLMCallCategory.COORDINATION,
        )
        assert record.call_category == LLMCallCategory.COORDINATION

    def test_call_category_system(self) -> None:
        """Accept SYSTEM call_category."""
        record = CostRecord(
            agent_id="agent-1",
            task_id="task-1",
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            call_category=LLMCallCategory.SYSTEM,
        )
        assert record.call_category == LLMCallCategory.SYSTEM

    def test_call_category_roundtrip(self) -> None:
        """Verify call_category survives JSON roundtrip."""
        record = CostRecord(
            agent_id="agent-1",
            task_id="task-1",
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            timestamp=datetime(2026, 2, 27, tzinfo=UTC),
            call_category=LLMCallCategory.PRODUCTIVE,
        )
        json_str = record.model_dump_json()
        restored = CostRecord.model_validate_json(json_str)
        assert restored.call_category == LLMCallCategory.PRODUCTIVE

    def test_factory(self) -> None:
        """Verify factory produces a valid instance."""
        record = CostRecordFactory.build()
        assert isinstance(record, CostRecord)
