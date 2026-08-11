"""Budget signal source -- reads cost metrics from budget tracker."""

from datetime import UTC, datetime
from typing import Final

from synthorg.budget.spending_summary import SpendingSummary, SpendMeasurability
from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.models import ScalingSignal
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_SIGNAL_COLLECTION_DEGRADED

logger = get_logger(__name__)

_SOURCE_NAME = NotBlankStr("budget")

#: What the budget reports when it cannot answer: fully burnt, at hard stop.
#: Conservative on purpose, because the consumer blocks hiring on a high burn
#: and does nothing at all on a missing signal.
_UNANSWERABLE_BURN_PERCENT: Final[float] = 100.0
_UNANSWERABLE_ALERT: Final[float] = 3.0

#: Name of the signal saying whether the burn figure beside it is a
#: measurement or the sentinel above. Emitted on every path, because a
#: consumer that has to infer it from the value cannot: a real estate can
#: genuinely be at 100%.
MEASURABLE_SIGNAL: Final[str] = "spend_measurable"
_MEASURABLE: Final[float] = 1.0
_NOT_MEASURABLE: Final[float] = 0.0


def _measurable_signal(now: datetime, *, measurable: bool) -> ScalingSignal:
    """Return the signal qualifying the burn figure emitted beside it."""
    return ScalingSignal(
        name=NotBlankStr(MEASURABLE_SIGNAL),
        value=_MEASURABLE if measurable else _NOT_MEASURABLE,
        source=_SOURCE_NAME,
        timestamp=now,
    )


def _cannot_answer(now: datetime) -> tuple[ScalingSignal, ...]:
    """Return the conservative signals for a budget that cannot answer.

    Args:
        now: Timestamp to stamp on the signals.

    Returns:
        A fully-burnt, hard-stop triple, so a consumer that blocks hiring on
        burn does so rather than reading silence as headroom, plus the
        qualifier saying the burn figure is a sentinel. Without the
        qualifier the operator-facing rationale reports a measurement that
        never happened: the hold is right, the stated reason is not.
    """
    return (
        ScalingSignal(
            name=NotBlankStr("burn_rate_percent"),
            value=_UNANSWERABLE_BURN_PERCENT,
            source=_SOURCE_NAME,
            timestamp=now,
        ),
        ScalingSignal(
            name=NotBlankStr("alert_level"),
            value=_UNANSWERABLE_ALERT,
            source=_SOURCE_NAME,
            timestamp=now,
        ),
        _measurable_signal(now, measurable=False),
    )


class BudgetSignalSource:
    """Read-only adapter over the budget subsystem.

    Converts ``SpendingSummary`` into scaling signals:
    ``burn_rate_percent``, ``alert_level``.
    """

    @property
    def name(self) -> NotBlankStr:
        """Source identifier."""
        return _SOURCE_NAME

    async def collect(
        self,
        agent_ids: tuple[NotBlankStr, ...],  # noqa: ARG002
        *,
        summary: SpendingSummary | None = None,
    ) -> tuple[ScalingSignal, ...]:
        """Collect budget signals from a spending summary.

        Args:
            agent_ids: Active agent IDs (not used directly).
            summary: Current spending summary from CostTracker.

        Returns:
            Budget signals: burn_rate_percent, alert_level.
        """
        now = datetime.now(UTC)

        if summary is None:
            logger.warning(
                HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
                source="budget",
                reason="no_spending_summary",
            )
            return _cannot_answer(now)

        if summary.measurability is not SpendMeasurability.MEASURED:
            # A window money cannot fully measure answers nothing about
            # headroom, so it takes the same conservative shape as no summary
            # at all. Emitting NOTHING would be worse than a wrong number:
            # ``budget_cap`` treats an absent burn signal as "no signal" and
            # returns no decision, which leaves hiring unblocked on exactly
            # the estate whose spend is invisible.
            logger.warning(
                HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
                source="budget",
                reason="spend_not_measurable",
                measurability=summary.measurability.value,
            )
            return _cannot_answer(now)

        # Map alert levels to numeric values for threshold comparison.
        alert_map = {
            "normal": 0.0,
            "warning": 1.0,
            "critical": 2.0,
            "hard_stop": 3.0,
        }
        alert_key = summary.alert_level.value
        if alert_key not in alert_map:
            logger.warning(
                HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
                source="budget",
                reason="unknown_alert_level",
                alert_level=alert_key,
            )
        alert_value = alert_map.get(alert_key, alert_map["hard_stop"])
        # MEASURED above is exactly the case that carries a percentage, so
        # this is total rather than defensive; ``or 0.0`` would silently
        # publish an unmeasurable window as zero burn.
        used_percent = summary.budget_used_percent
        if used_percent is None:
            return _cannot_answer(now)

        return (
            ScalingSignal(
                name=NotBlankStr("burn_rate_percent"),
                value=round(used_percent, 4),
                source=_SOURCE_NAME,
                timestamp=now,
            ),
            ScalingSignal(
                name=NotBlankStr("alert_level"),
                value=alert_value,
                source=_SOURCE_NAME,
                timestamp=now,
            ),
            _measurable_signal(now, measurable=True),
        )
