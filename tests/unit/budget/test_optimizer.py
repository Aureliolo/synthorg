"""Tests for CostOptimizer — init, classify severity, input validation."""

import pytest

from synthorg.budget._optimizer_helpers import _classify_severity
from synthorg.budget.optimizer_models import (
    AnomalySeverity,
    CostOptimizerConfig,
)
from tests.unit.budget.conftest import OPT_END, OPT_START, make_optimizer

# ── Init Tests ────────────────────────────────────────────────────


@pytest.mark.unit
class TestInit:
    async def test_defaults(self) -> None:
        optimizer, _ = make_optimizer()
        assert optimizer._config == CostOptimizerConfig()

    async def test_custom_config(self) -> None:
        cfg = CostOptimizerConfig(anomaly_sigma_threshold=3.0)
        optimizer, _ = make_optimizer(config=cfg)
        assert optimizer._config.anomaly_sigma_threshold == 3.0


# ── _classify_severity Tests ─────────────────────────────────────


@pytest.mark.unit
class TestClassifySeverity:
    @pytest.mark.parametrize(
        ("deviation", "expected"),
        [
            (0.0, AnomalySeverity.LOW),
            (1.5, AnomalySeverity.LOW),
            (1.99, AnomalySeverity.LOW),
            (2.0, AnomalySeverity.MEDIUM),
            (2.5, AnomalySeverity.MEDIUM),
            (2.99, AnomalySeverity.MEDIUM),
            (3.0, AnomalySeverity.HIGH),
            (5.0, AnomalySeverity.HIGH),
            (100.0, AnomalySeverity.HIGH),
        ],
    )
    def test_thresholds(self, deviation: float, expected: AnomalySeverity) -> None:
        assert _classify_severity(deviation) == expected


# ── Input Validation Tests ───────────────────────────────────────


@pytest.mark.unit
class TestInputValidation:
    async def test_detect_anomalies_start_after_end(self) -> None:
        optimizer, _ = make_optimizer()
        with pytest.raises(ValueError, match=r"start .* must be before end"):
            await optimizer.detect_anomalies(start=OPT_END, end=OPT_START)

    async def test_analyze_efficiency_start_after_end(self) -> None:
        optimizer, _ = make_optimizer()
        with pytest.raises(ValueError, match=r"start .* must be before end"):
            await optimizer.analyze_efficiency(start=OPT_END, end=OPT_START)

    async def test_recommend_downgrades_start_after_end(self) -> None:
        optimizer, _ = make_optimizer()
        with pytest.raises(ValueError, match=r"start .* must be before end"):
            await optimizer.recommend_downgrades(start=OPT_END, end=OPT_START)
