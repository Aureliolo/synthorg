"""Time-based risk-elevation classifier.

Wraps a base classifier and elevates one tier during configured
off-hours and (optionally) weekends -- riskier to action an approval
when fewer humans are watching. Time is read through the ``Clock``
seam so the window evaluation is deterministic under ``FakeClock`` in
tests. The window is evaluated in the clock's timezone (UTC for
``SystemClock``).
"""

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.clock import Clock, SystemClock
from synthorg.security.timeout.protocol import RiskTierClassifier
from synthorg.security.timeout.risk_tier_classifier import elevate_one_tier

_SATURDAY: int = 5  # datetime.weekday(): Mon=0 .. Sun=6
_MIN_HOUR_OF_DAY: int = 0
_MAX_HOUR_OF_DAY: int = 23


class TimeBasedRiskElevationClassifier:
    """Elevate one tier during off-hours / weekend windows.

    Args:
        base: The classifier whose verdict is elevated off-hours.
        off_hours_start_hour: Inclusive start of the off-hours window
            (0-23, clock timezone).
        off_hours_end_hour: Exclusive end of the off-hours window
            (0-23). A start greater than the end denotes a window that
            wraps midnight (e.g. ``20`` -> ``6``).
        weekend_elevation: When ``True``, Saturday/Sunday always
            elevate regardless of the hour window.
        clock: Clock seam; defaults to :class:`SystemClock`.
    """

    def __init__(
        self,
        *,
        base: RiskTierClassifier,
        off_hours_start_hour: int,
        off_hours_end_hour: int,
        weekend_elevation: bool,
        clock: Clock | None = None,
    ) -> None:
        for field_name, hour in (
            ("off_hours_start_hour", off_hours_start_hour),
            ("off_hours_end_hour", off_hours_end_hour),
        ):
            if not _MIN_HOUR_OF_DAY <= hour <= _MAX_HOUR_OF_DAY:
                msg = (
                    f"{field_name} must be in"
                    f" [{_MIN_HOUR_OF_DAY}, {_MAX_HOUR_OF_DAY}], got {hour}"
                )
                raise ValueError(msg)
        self._base = base
        self._start = off_hours_start_hour
        self._end = off_hours_end_hour
        self._weekend_elevation = weekend_elevation
        self._clock: Clock = clock if clock is not None else SystemClock()

    def classify(self, action_type: str) -> ApprovalRiskLevel:
        """Classify, elevating one tier inside the off-hours window.

        Returns:
            The base risk level, elevated one tier when the current
            time falls inside the off-hours window.
        """
        level = self._base.classify(action_type)
        if self._is_elevated_window():
            return elevate_one_tier(level)
        return level

    def _is_elevated_window(self) -> bool:
        """Report whether the current time is in the off-hours window.

        Returns:
            ``True`` during the weekend (when enabled) or inside the
            configured off-hours window (which may wrap midnight).
        """
        now = self._clock.now()
        if self._weekend_elevation and now.weekday() >= _SATURDAY:
            return True
        hour = now.hour
        if self._start == self._end:
            # Degenerate window: no off-hours elevation by hour.
            return False
        if self._start < self._end:
            return self._start <= hour < self._end
        # Wraps midnight (e.g. 20..6): in-window if at/after start OR
        # before end.
        return hour >= self._start or hour < self._end
