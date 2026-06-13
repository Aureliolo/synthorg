"""Tests for LlmCalibrationSampler."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.llm_calibration_sampler import LlmCalibrationSampler
from synthorg.providers.models import CompletionResponse, TokenUsage
from tests._shared import FakeClock

from .conftest import make_calibration_record, make_collab_metric

NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


def _make_provider(
    *,
    content: str = '{"score": 7.5, "rationale": "Good collaboration"}',
    cost: float = 0.001,
) -> AsyncMock:
    """Build a mock CompletionProvider."""
    provider = AsyncMock()
    provider.complete.return_value = CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=100, output_tokens=50, cost=cost),
        model=NotBlankStr("test-small-001"),
    )
    return provider


def _make_sampler(
    *,
    provider: AsyncMock | None = None,
    sampling_rate: float = 1.0,
    retention_days: int = 90,
    clock: FakeClock | None = None,
) -> LlmCalibrationSampler:
    """Build a sampler with sensible defaults (100% rate for testing).

    The clock defaults to a :class:`FakeClock` pinned at ``NOW`` so the
    retention-pruning cutoff is deterministic regardless of wall-clock
    time (the prune window is ``now - retention_days``).
    """
    return LlmCalibrationSampler(
        provider=provider or _make_provider(),
        model=NotBlankStr("test-small-001"),
        sampling_rate=sampling_rate,
        retention_days=retention_days,
        clock=clock or FakeClock(start=NOW),
    )


@pytest.mark.unit
class TestConstructorValidation:
    """Constructor input validation."""

    def test_sampling_rate_below_zero_raises(self) -> None:
        """Sampling rate below 0.0 raises ValueError."""
        with pytest.raises(ValueError, match="sampling_rate must be in"):
            _make_sampler(sampling_rate=-0.1)

    def test_sampling_rate_above_one_raises(self) -> None:
        """Sampling rate above 1.0 raises ValueError."""
        with pytest.raises(ValueError, match="sampling_rate must be in"):
            _make_sampler(sampling_rate=1.1)

    def test_retention_days_zero_raises(self) -> None:
        """Zero retention days raises ValueError."""
        with pytest.raises(ValueError, match="retention_days must be >= 1"):
            _make_sampler(retention_days=0)

    def test_retention_days_negative_raises(self) -> None:
        """Negative retention days raises ValueError."""
        with pytest.raises(ValueError, match="retention_days must be >= 1"):
            _make_sampler(retention_days=-5)


@pytest.mark.unit
class TestShouldSample:
    """Probabilistic sampling decision."""

    @patch("synthorg.hr.performance.llm_calibration_sampler.random")
    def test_below_rate_returns_true(self, mock_random: AsyncMock) -> None:
        """Random value below rate -> should sample."""
        mock_random.random.return_value = 0.005
        sampler = _make_sampler(sampling_rate=0.01)

        assert sampler.should_sample() is True

    @patch("synthorg.hr.performance.llm_calibration_sampler.random")
    def test_above_rate_returns_false(self, mock_random: AsyncMock) -> None:
        """Random value above rate -> should not sample."""
        mock_random.random.return_value = 0.5
        sampler = _make_sampler(sampling_rate=0.01)

        assert sampler.should_sample() is False

    @patch("synthorg.hr.performance.llm_calibration_sampler.random")
    def test_zero_rate_never_samples(self, mock_random: AsyncMock) -> None:
        """Zero sampling rate never triggers."""
        mock_random.random.return_value = 0.0
        sampler = _make_sampler(sampling_rate=0.0)

        # Even with random=0.0, rate=0.0 means 0.0 < 0.0 is False
        assert sampler.should_sample() is False


@pytest.mark.unit
class TestSample:
    """LLM-based collaboration evaluation."""

    async def test_successful_sample(self) -> None:
        """Successful LLM call produces a calibration record."""
        provider = _make_provider()
        sampler = _make_sampler(provider=provider)
        record = make_collab_metric(
            recorded_at=NOW,
            delegation_success=True,
            interaction_summary="Agent delegated task successfully",
        )

        result = await sampler.sample(
            record=record,
            behavioral_score=6.0,
        )

        assert result is not None
        assert result.llm_score == 7.5
        assert result.behavioral_score == 6.0
        assert result.drift == 1.5
        assert result.rationale == "Good collaboration"
        assert result.model_used == "test-small-001"
        assert result.cost == 0.001
        assert result.agent_id == "agent-001"
        assert result.interaction_record_id == str(record.id)

    async def test_skips_record_without_summary(self) -> None:
        """Records without interaction_summary are skipped."""
        sampler = _make_sampler()
        record = make_collab_metric(
            recorded_at=NOW,
            delegation_success=True,
        )

        result = await sampler.sample(
            record=record,
            behavioral_score=6.0,
        )

        assert result is None

    async def test_provider_failure_returns_none(self) -> None:
        """Provider exception is caught, returns None."""
        provider = AsyncMock()
        provider.complete.side_effect = RuntimeError("LLM unavailable")
        sampler = _make_sampler(provider=provider)
        record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Some interaction",
        )

        result = await sampler.sample(
            record=record,
            behavioral_score=6.0,
        )

        assert result is None

    async def test_malformed_json_returns_none(self) -> None:
        """Unparseable LLM response returns None."""
        provider = _make_provider(content="not valid json")
        sampler = _make_sampler(provider=provider)
        record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Some interaction",
        )

        result = await sampler.sample(
            record=record,
            behavioral_score=6.0,
        )

        assert result is None

    async def test_drift_is_absolute_difference(self) -> None:
        """Drift is abs(llm_score - behavioral_score)."""
        provider = _make_provider(
            content='{"score": 3.0, "rationale": "Below average"}',
        )
        sampler = _make_sampler(provider=provider)
        record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Some interaction",
        )

        result = await sampler.sample(
            record=record,
            behavioral_score=8.0,
        )

        assert result is not None
        assert result.drift == 5.0

    async def test_null_content_returns_none(self) -> None:
        """LLM returning no content produces None."""
        provider = AsyncMock()
        provider.complete.return_value = CompletionResponse(
            content=None,
            tool_calls=(
                # Need a tool call since content is None and finish_reason is STOP
                # Actually, content_filter finish reason allows None content
            ),
            finish_reason=FinishReason.CONTENT_FILTER,
            usage=TokenUsage(input_tokens=10, output_tokens=0, cost=0.0),
            model=NotBlankStr("test-small-001"),
        )
        sampler = _make_sampler(provider=provider)
        record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Some interaction",
        )

        result = await sampler.sample(record=record, behavioral_score=5.0)

        assert result is None

    @pytest.mark.parametrize(
        "score_val",
        [15.0, -1.0],
        ids=["above_max", "below_min"],
    )
    async def test_out_of_range_score_returns_none(
        self,
        score_val: float,
    ) -> None:
        """LLM returning score outside [0, 10] produces None."""
        provider = _make_provider(
            content=f'{{"score": {score_val}, "rationale": "Bad range"}}',
        )
        sampler = _make_sampler(provider=provider)
        record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Some interaction",
        )

        result = await sampler.sample(record=record, behavioral_score=5.0)

        assert result is None

    async def test_blank_rationale_returns_none(self) -> None:
        """LLM returning whitespace-only rationale produces None."""
        provider = _make_provider(
            content='{"score": 7.0, "rationale": "   "}',
        )
        sampler = _make_sampler(provider=provider)
        record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Some interaction",
        )

        result = await sampler.sample(record=record, behavioral_score=5.0)

        assert result is None

    async def test_record_stored_after_sample(self) -> None:
        """Calibration records are stored for later retrieval."""
        sampler = _make_sampler()
        record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Some interaction",
        )

        await sampler.sample(record=record, behavioral_score=6.0)

        records = sampler.get_calibration_records(
            agent_id=NotBlankStr("agent-001"),
        )
        assert len(records) == 1


@pytest.mark.unit
class TestGetCalibrationRecords:
    """Querying stored calibration records."""

    async def test_filter_by_agent(self) -> None:
        """Records can be filtered by agent_id."""
        sampler = _make_sampler()
        r1 = make_collab_metric(
            agent_id="agent-001",
            recorded_at=NOW,
            interaction_summary="Interaction A",
        )
        r2 = make_collab_metric(
            agent_id="agent-002",
            recorded_at=NOW,
            interaction_summary="Interaction B",
        )
        await sampler.sample(record=r1, behavioral_score=5.0)
        await sampler.sample(record=r2, behavioral_score=5.0)

        agent1_records = sampler.get_calibration_records(
            agent_id=NotBlankStr("agent-001"),
        )
        all_records = sampler.get_calibration_records()

        assert len(agent1_records) == 1
        assert len(all_records) == 2

    def test_filter_by_since(self) -> None:
        """Records can be filtered by sampled_at time."""
        # Anchor the seeded records to the real clock rather than the fixed
        # NOW constant. get_calibration_records prunes records older than
        # retention_days against the wall clock, so a fixed past timestamp
        # gets evicted before the ``since`` filter runs once real time moves
        # more than retention_days past it (a calendar-driven time bomb).
        # Relative-to-now records always sit inside the default window, so the
        # test exercises the since filter deterministically on any date.
        now = datetime.now(UTC)
        sampler = _make_sampler()
        old_cal = make_calibration_record(
            agent_id="agent-001",
            sampled_at=now - timedelta(days=10),
        )
        recent_cal = make_calibration_record(
            agent_id="agent-001",
            sampled_at=now,
        )
        # Directly populate internal storage for time-sensitive test.
        sampler._records["agent-001"] = [old_cal, recent_cal]

        since_records = sampler.get_calibration_records(
            since=now - timedelta(days=5),
        )

        assert len(since_records) == 1
        assert since_records[0].sampled_at == now


@pytest.mark.unit
class TestGetDriftSummary:
    """Average drift computation."""

    async def test_no_records_returns_none(self) -> None:
        """No calibration records -> None."""
        sampler = _make_sampler()

        drift = sampler.get_drift_summary(NotBlankStr("agent-001"))

        assert drift is None

    async def test_average_drift(self) -> None:
        """Average drift across multiple records."""
        provider = _make_provider(
            content='{"score": 7.0, "rationale": "Good"}',
        )
        sampler = _make_sampler(provider=provider)
        r1 = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Interaction 1",
        )
        r2 = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="Interaction 2",
        )
        # behavioral=5.0 -> llm=7.0 -> drift=2.0 each
        await sampler.sample(record=r1, behavioral_score=5.0)
        await sampler.sample(record=r2, behavioral_score=5.0)

        drift = sampler.get_drift_summary(NotBlankStr("agent-001"))

        assert drift == 2.0


@pytest.mark.unit
class TestRetentionPruning:
    """Old calibration records are pruned."""

    async def test_old_records_pruned(self) -> None:
        """Records older than retention_days are pruned on next sample."""
        # The sampler's clock is pinned at NOW (FakeClock default), so the
        # pruning cutoff (now - retention_days) is deterministic.
        sampler = _make_sampler(retention_days=7)
        # Insert an old calibration record directly.
        old_cal = make_calibration_record(
            agent_id="agent-001",
            sampled_at=NOW - timedelta(days=10),
            interaction_record_id="old-record",
        )
        sampler._records["agent-001"] = [old_cal]

        # Sample a new record -- triggers pruning of old records.
        new_record = make_collab_metric(
            recorded_at=NOW,
            interaction_summary="New interaction",
        )
        await sampler.sample(record=new_record, behavioral_score=5.0)

        # Old record should be pruned, only new remains.
        records = sampler.get_calibration_records()
        assert len(records) == 1
        assert records[0].interaction_record_id == str(new_record.id)
