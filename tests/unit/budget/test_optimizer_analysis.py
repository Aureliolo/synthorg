"""Tests for CostOptimizer — anomaly detection and efficiency analysis."""

from datetime import timedelta

import pytest

from synthorg.budget.optimizer_models import (
    AnomalySeverity,
    AnomalyType,
    CostOptimizerConfig,
    EfficiencyRating,
)
from tests.unit.budget.conftest import (
    OPT_END,
    OPT_START,
    make_cost_record,
    make_optimizer,
)

# ── Anomaly Detection Tests ──────────────────────────────────────


@pytest.mark.unit
class TestDetectAnomalies:
    async def test_no_records_empty_result(self) -> None:
        optimizer, _ = make_optimizer()
        result = await optimizer.detect_anomalies(start=OPT_START, end=OPT_END)
        assert result.anomalies == ()
        assert result.agents_scanned == 0

    async def test_normal_spending_no_anomalies(self) -> None:
        optimizer, tracker = make_optimizer()
        window_duration = (OPT_END - OPT_START) / 5
        for i in range(5):
            ts = OPT_START + window_duration * i + timedelta(hours=1)
            await tracker.record(
                make_cost_record(agent_id="alice", cost_usd=1.0, timestamp=ts),
            )

        result = await optimizer.detect_anomalies(start=OPT_START, end=OPT_END)
        assert result.anomalies == ()
        assert result.agents_scanned == 1

    async def test_spike_detected(self) -> None:
        optimizer, tracker = make_optimizer()
        window_duration = (OPT_END - OPT_START) / 5

        for i in range(4):
            ts = OPT_START + window_duration * i + timedelta(hours=1)
            await tracker.record(
                make_cost_record(agent_id="alice", cost_usd=1.0, timestamp=ts),
            )

        ts = OPT_START + window_duration * 4 + timedelta(hours=1)
        await tracker.record(
            make_cost_record(agent_id="alice", cost_usd=20.0, timestamp=ts),
        )

        result = await optimizer.detect_anomalies(start=OPT_START, end=OPT_END)
        assert len(result.anomalies) == 1
        anomaly = result.anomalies[0]
        assert anomaly.agent_id == "alice"
        assert anomaly.anomaly_type == AnomalyType.SPIKE
        assert anomaly.current_value == 20.0

    async def test_insufficient_windows_no_false_positive(self) -> None:
        config = CostOptimizerConfig(min_anomaly_windows=5)
        optimizer, tracker = make_optimizer(config=config)

        window_duration = (OPT_END - OPT_START) / 3
        for i in range(3):
            ts = OPT_START + window_duration * i + timedelta(hours=1)
            cost = 1.0 if i < 2 else 50.0
            await tracker.record(
                make_cost_record(agent_id="alice", cost_usd=cost, timestamp=ts),
            )

        result = await optimizer.detect_anomalies(
            start=OPT_START,
            end=OPT_END,
            window_count=3,
        )
        assert result.anomalies == ()

    async def test_multiple_agents_only_anomalous_flagged(self) -> None:
        optimizer, tracker = make_optimizer()
        window_duration = (OPT_END - OPT_START) / 5

        for i in range(5):
            ts = OPT_START + window_duration * i + timedelta(hours=1)
            await tracker.record(
                make_cost_record(agent_id="alice", cost_usd=1.0, timestamp=ts),
            )

        for i in range(4):
            ts = OPT_START + window_duration * i + timedelta(hours=1)
            await tracker.record(
                make_cost_record(agent_id="bob", cost_usd=1.0, timestamp=ts),
            )
        ts = OPT_START + window_duration * 4 + timedelta(hours=1)
        await tracker.record(
            make_cost_record(agent_id="bob", cost_usd=20.0, timestamp=ts),
        )

        result = await optimizer.detect_anomalies(start=OPT_START, end=OPT_END)
        assert len(result.anomalies) == 1
        assert result.anomalies[0].agent_id == "bob"
        assert result.agents_scanned == 2

    async def test_window_count_validation(self) -> None:
        optimizer, _ = make_optimizer()
        with pytest.raises(ValueError, match="window_count must be >= 2"):
            await optimizer.detect_anomalies(
                start=OPT_START,
                end=OPT_END,
                window_count=1,
            )

    async def test_spike_from_zero_baseline(self) -> None:
        """Agent with no historical spending that suddenly appears."""
        optimizer, tracker = make_optimizer(
            config=CostOptimizerConfig(min_anomaly_windows=3),
        )
        window_duration = (OPT_END - OPT_START) / 5

        ts = OPT_START + window_duration * 4 + timedelta(hours=1)
        await tracker.record(
            make_cost_record(agent_id="alice", cost_usd=5.0, timestamp=ts),
        )

        result = await optimizer.detect_anomalies(start=OPT_START, end=OPT_END)
        assert len(result.anomalies) == 1
        anomaly = result.anomalies[0]
        assert anomaly.severity == AnomalySeverity.HIGH
        assert anomaly.baseline_value == 0.0

    async def test_spike_severity_with_zero_stddev(self) -> None:
        """Spike severity uses spike_ratio when stddev is 0."""
        optimizer, tracker = make_optimizer(
            config=CostOptimizerConfig(
                anomaly_sigma_threshold=2.0,
                anomaly_spike_factor=2.0,
                min_anomaly_windows=3,
            ),
        )
        window_duration = (OPT_END - OPT_START) / 5

        for i in range(4):
            ts = OPT_START + window_duration * i + timedelta(hours=1)
            await tracker.record(
                make_cost_record(agent_id="alice", cost_usd=1.0, timestamp=ts),
            )

        ts = OPT_START + window_duration * 4 + timedelta(hours=1)
        await tracker.record(
            make_cost_record(agent_id="alice", cost_usd=4.0, timestamp=ts),
        )

        result = await optimizer.detect_anomalies(start=OPT_START, end=OPT_END)
        assert len(result.anomalies) == 1
        assert result.anomalies[0].severity == AnomalySeverity.HIGH

    async def test_window_count_upper_bound(self) -> None:
        """window_count > 1000 raises ValueError."""
        optimizer, _ = make_optimizer()
        with pytest.raises(ValueError, match="window_count must be <= 1000"):
            await optimizer.detect_anomalies(
                start=OPT_START,
                end=OPT_END,
                window_count=1001,
            )


# ── Efficiency Analysis Tests ─────────────────────────────────────


@pytest.mark.unit
class TestAnalyzeEfficiency:
    async def test_uniform_all_normal(self) -> None:
        optimizer, tracker = make_optimizer()

        for agent in ("alice", "bob", "carol"):
            await tracker.record(
                make_cost_record(
                    agent_id=agent,
                    cost_usd=1.0,
                    input_tokens=1000,
                    output_tokens=0,
                    timestamp=OPT_START + timedelta(hours=1),
                ),
            )

        result = await optimizer.analyze_efficiency(start=OPT_START, end=OPT_END)
        assert all(
            a.efficiency_rating == EfficiencyRating.NORMAL for a in result.agents
        )
        assert result.inefficient_agent_count == 0

    async def test_one_inefficient(self) -> None:
        optimizer, tracker = make_optimizer()

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=1.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )
        await tracker.record(
            make_cost_record(
                agent_id="bob",
                cost_usd=10.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.analyze_efficiency(start=OPT_START, end=OPT_END)
        assert result.inefficient_agent_count == 1
        assert result.agents[0].agent_id == "bob"
        assert result.agents[0].efficiency_rating == EfficiencyRating.INEFFICIENT

    async def test_zero_tokens_handled(self) -> None:
        optimizer, tracker = make_optimizer()

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=0.0,
                input_tokens=0,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.analyze_efficiency(start=OPT_START, end=OPT_END)
        assert len(result.agents) == 1
        assert result.agents[0].cost_per_1k_tokens == 0.0
        assert result.agents[0].efficiency_rating == EfficiencyRating.NORMAL

    async def test_efficient_agent_flagged(self) -> None:
        optimizer, tracker = make_optimizer()

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                cost_usd=0.1,
                input_tokens=10000,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )
        await tracker.record(
            make_cost_record(
                agent_id="bob",
                cost_usd=1.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )
        await tracker.record(
            make_cost_record(
                agent_id="carol",
                cost_usd=1.0,
                input_tokens=1000,
                output_tokens=0,
                timestamp=OPT_START + timedelta(hours=1),
            ),
        )

        result = await optimizer.analyze_efficiency(start=OPT_START, end=OPT_END)
        alice = next(a for a in result.agents if a.agent_id == "alice")
        assert alice.efficiency_rating == EfficiencyRating.EFFICIENT

    async def test_empty_records(self) -> None:
        optimizer, _ = make_optimizer()
        result = await optimizer.analyze_efficiency(start=OPT_START, end=OPT_END)
        assert result.agents == ()
        assert result.global_avg_cost_per_1k == 0.0
