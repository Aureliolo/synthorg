"""Tests for BaselineStore sliding-window store."""

import pytest
from pydantic import ValidationError

from synthorg.budget.baseline_store import BaselineRecord, BaselineStore


def _record(
    *,
    agent_id: str = "agent-1",
    task_id: str = "task-1",
    turns: float = 5.0,
    error_rate: float = 0.1,
    total_tokens: float = 1000.0,
    duration_seconds: float = 10.0,
) -> BaselineRecord:
    return BaselineRecord(
        agent_id=agent_id,
        task_id=task_id,
        turns=turns,
        error_rate=error_rate,
        total_tokens=total_tokens,
        duration_seconds=duration_seconds,
    )


@pytest.mark.unit
class TestBaselineRecord:
    """BaselineRecord model validation."""

    def test_basic_construction(self) -> None:
        r = _record()
        assert r.agent_id == "agent-1"
        assert r.turns == 5.0
        assert r.error_rate == 0.1
        assert r.total_tokens == 1000.0
        assert r.duration_seconds == 10.0

    def test_timestamp_auto_set(self) -> None:
        r = _record()
        assert r.timestamp is not None

    def test_frozen(self) -> None:
        r = _record()
        with pytest.raises(ValidationError):
            r.turns = 99.0  # type: ignore[misc]

    def test_turns_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            _record(turns=0.0)

    def test_error_rate_bounds(self) -> None:
        _record(error_rate=0.0)
        _record(error_rate=1.0)
        with pytest.raises(ValidationError):
            _record(error_rate=-0.1)
        with pytest.raises(ValidationError):
            _record(error_rate=1.1)

    def test_total_tokens_non_negative(self) -> None:
        _record(total_tokens=0.0)
        with pytest.raises(ValidationError):
            _record(total_tokens=-1.0)

    def test_duration_non_negative(self) -> None:
        _record(duration_seconds=0.0)
        with pytest.raises(ValidationError):
            _record(duration_seconds=-0.5)


@pytest.mark.unit
class TestBaselineStore:
    """BaselineStore sliding-window behaviour."""

    def test_empty_store_returns_none(self) -> None:
        store = BaselineStore(window_size=10)
        assert store.get_baseline_turns() is None
        assert store.get_baseline_error_rate() is None
        assert store.get_baseline_tokens() is None
        assert store.get_baseline_duration() is None

    def test_len_starts_at_zero(self) -> None:
        store = BaselineStore(window_size=10)
        assert len(store) == 0

    def test_single_record(self) -> None:
        store = BaselineStore(window_size=10)
        store.record(
            _record(
                turns=8.0, error_rate=0.2, total_tokens=500.0, duration_seconds=15.0
            )
        )
        assert store.get_baseline_turns() == pytest.approx(8.0)
        assert store.get_baseline_error_rate() == pytest.approx(0.2)
        assert store.get_baseline_tokens() == pytest.approx(500.0)
        assert store.get_baseline_duration() == pytest.approx(15.0)
        assert len(store) == 1

    def test_mean_across_multiple_records(self) -> None:
        store = BaselineStore(window_size=10)
        store.record(
            _record(turns=4.0, error_rate=0.1, total_tokens=800.0, duration_seconds=8.0)
        )
        store.record(
            _record(
                turns=6.0, error_rate=0.3, total_tokens=1200.0, duration_seconds=12.0
            )
        )
        assert store.get_baseline_turns() == pytest.approx(5.0)
        assert store.get_baseline_error_rate() == pytest.approx(0.2)
        assert store.get_baseline_tokens() == pytest.approx(1000.0)
        assert store.get_baseline_duration() == pytest.approx(10.0)

    def test_window_eviction(self) -> None:
        store = BaselineStore(window_size=3)
        for i in range(5):
            store.record(_record(turns=float(i + 1)))
        # window_size=3 means only last 3 kept: turns 3, 4, 5
        assert len(store) == 3
        assert store.get_baseline_turns() == pytest.approx(4.0)  # mean(3,4,5)

    def test_window_size_one(self) -> None:
        store = BaselineStore(window_size=1)
        store.record(_record(turns=10.0))
        store.record(_record(turns=20.0))
        assert len(store) == 1
        assert store.get_baseline_turns() == pytest.approx(20.0)

    def test_invalid_window_size(self) -> None:
        with pytest.raises(ValueError, match="window_size must be positive"):
            BaselineStore(window_size=0)
        with pytest.raises(ValueError, match="window_size must be positive"):
            BaselineStore(window_size=-5)

    def test_len_tracks_record_count(self) -> None:
        store = BaselineStore(window_size=100)
        for i in range(10):
            store.record(_record(agent_id=f"agent-{i}"))
        assert len(store) == 10
