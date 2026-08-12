"""Tests for budget signal source."""

from datetime import UTC, datetime

import pytest

from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.spending_summary import (
    PeriodSpending,
    SpendingSummary,
    SpendMeasurability,
)
from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.signals.budget import (
    MEASURABLE_SIGNAL,
    BudgetSignalSource,
)

_AGENT_IDS = (NotBlankStr("a1"),)
_START = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
_END = datetime(2026, 4, 30, 23, 59, 59, tzinfo=UTC)


def _make_summary(
    *,
    used_percent: float | None = 50.0,
    alert: BudgetAlertLevel = BudgetAlertLevel.NORMAL,
    measurability: SpendMeasurability = SpendMeasurability.MEASURED,
) -> SpendingSummary:
    return SpendingSummary(
        period=PeriodSpending(
            total_cost=100.0,
            currency="EUR",
            record_count=10,
            start=_START,
            end=_END,
        ),
        budget_total_monthly=1000.0,
        budget_used_percent=used_percent,
        alert_level=alert,
        measurability=measurability,
    )


@pytest.mark.unit
class TestBudgetSignalSource:
    """BudgetSignalSource signal collection."""

    async def test_no_summary_fails_closed_returns_max_burn_and_hard_stop(
        self,
    ) -> None:
        source = BudgetSignalSource()
        signals = await source.collect(_AGENT_IDS, summary=None)
        by_name = {s.name: s.value for s in signals}
        # Fail-closed: assume maximum burn and hard-stop alert when summary unavailable
        assert by_name["burn_rate_percent"] == 100.0
        assert by_name["alert_level"] == 3.0

    @pytest.mark.parametrize(
        ("used_percent", "alert_level_enum", "expected_alert_value"),
        [
            (30.0, BudgetAlertLevel.NORMAL, 0.0),
            (70.0, BudgetAlertLevel.WARNING, 1.0),
            (90.0, BudgetAlertLevel.CRITICAL, 2.0),
            (100.0, BudgetAlertLevel.HARD_STOP, 3.0),
        ],
        ids=[
            "normal-alert-level",
            "warning-alert-level",
            "critical-alert-level",
            "hard-stop-alert-level",
        ],
    )
    async def test_alert_level_mapping(
        self,
        used_percent: float,
        alert_level_enum: BudgetAlertLevel,
        expected_alert_value: float,
    ) -> None:
        source = BudgetSignalSource()
        summary = _make_summary(used_percent=used_percent, alert=alert_level_enum)
        signals = await source.collect(_AGENT_IDS, summary=summary)
        by_name = {s.name: s.value for s in signals}
        assert by_name["burn_rate_percent"] == used_percent
        assert by_name["alert_level"] == expected_alert_value

    @pytest.mark.parametrize(
        "measurability",
        [SpendMeasurability.UNMEASURABLE, SpendMeasurability.MIXED],
    )
    async def test_an_unmeasurable_window_fails_closed(
        self,
        measurability: SpendMeasurability,
    ) -> None:
        # The window money cannot measure is exactly the one where hiring
        # must not read silence as headroom: emitting nothing leaves
        # ``budget_cap`` with no burn signal, which it treats as "no signal"
        # and returns no decision at all.
        source = BudgetSignalSource()
        summary = _make_summary(used_percent=None, measurability=measurability)
        signals = await source.collect(_AGENT_IDS, summary=summary)
        by_name = {s.name: s.value for s in signals}
        assert by_name["burn_rate_percent"] == 100.0
        assert by_name["alert_level"] == 3.0

    async def test_the_sentinel_is_marked_as_one(self) -> None:
        # 100% is a value a real estate can genuinely reach, so the burn
        # figure alone cannot say whether it was measured. Without the
        # qualifier the operator-facing rationale reports a measurement
        # that never happened.
        source = BudgetSignalSource()
        unmeasured = await source.collect(
            _AGENT_IDS,
            summary=_make_summary(
                used_percent=None,
                measurability=SpendMeasurability.UNMEASURABLE,
            ),
        )
        measured = await source.collect(_AGENT_IDS, summary=_make_summary())
        assert {s.name: s.value for s in unmeasured}[MEASURABLE_SIGNAL] == 0.0
        assert {s.name: s.value for s in measured}[MEASURABLE_SIGNAL] == 1.0

    async def test_no_summary_is_also_marked_unmeasured(self) -> None:
        source = BudgetSignalSource()
        signals = await source.collect(_AGENT_IDS, summary=None)
        assert {s.name: s.value for s in signals}[MEASURABLE_SIGNAL] == 0.0

    async def test_source_name(self) -> None:
        source = BudgetSignalSource()
        assert source.name == "budget"

    async def test_signal_source_field(self) -> None:
        source = BudgetSignalSource()
        summary = _make_summary()
        signals = await source.collect(_AGENT_IDS, summary=summary)
        assert all(s.source == "budget" for s in signals)
