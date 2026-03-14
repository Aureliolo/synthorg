"""Meeting frequency enum and conversion (see Communication design page).

Defines recurrence intervals for scheduled meetings.
"""

from enum import StrEnum
from types import MappingProxyType
from typing import Final

# Interval constants in seconds.
_SECONDS_PER_DAY: Final[float] = 86_400.0
_SECONDS_PER_WEEK: Final[float] = 604_800.0
_SECONDS_PER_BI_WEEK: Final[float] = 1_209_600.0
_SECONDS_PER_MONTH: Final[float] = 2_592_000.0  # 30 days


class MeetingFrequency(StrEnum):
    """Supported meeting recurrence schedules.

    Members:
        DAILY: Once per day.
        WEEKLY: Once per week.
        BI_WEEKLY: Once every two weeks.
        PER_SPRINT_DAY: Once per sprint day (same interval as daily).
        MONTHLY: Once per month (30-day approximation).
    """

    DAILY = "daily"
    WEEKLY = "weekly"
    BI_WEEKLY = "bi_weekly"
    PER_SPRINT_DAY = "per_sprint_day"
    MONTHLY = "monthly"


_FREQUENCY_SECONDS: MappingProxyType[MeetingFrequency, float] = MappingProxyType(
    {
        MeetingFrequency.DAILY: _SECONDS_PER_DAY,
        MeetingFrequency.WEEKLY: _SECONDS_PER_WEEK,
        MeetingFrequency.BI_WEEKLY: _SECONDS_PER_BI_WEEK,
        MeetingFrequency.PER_SPRINT_DAY: _SECONDS_PER_DAY,
        MeetingFrequency.MONTHLY: _SECONDS_PER_MONTH,
    }
)

# Ensure every enum member has a mapping entry.
assert set(_FREQUENCY_SECONDS) == set(MeetingFrequency), (  # noqa: S101
    f"Missing frequency mapping for: {set(MeetingFrequency) - set(_FREQUENCY_SECONDS)}"
)


def frequency_to_seconds(freq: MeetingFrequency) -> float:
    """Convert a frequency enum to its interval in seconds.

    Args:
        freq: The meeting frequency.

    Returns:
        Interval in seconds between meeting occurrences.
    """
    return _FREQUENCY_SECONDS[freq]
