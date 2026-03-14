"""Tests for meeting frequency enum and conversion."""

import pytest

from synthorg.communication.meeting.frequency import (
    MeetingFrequency,
    frequency_to_seconds,
)


@pytest.mark.unit
class TestMeetingFrequency:
    """Tests for MeetingFrequency enum."""

    @pytest.mark.parametrize(
        ("value", "member"),
        [
            ("daily", MeetingFrequency.DAILY),
            ("weekly", MeetingFrequency.WEEKLY),
            ("bi_weekly", MeetingFrequency.BI_WEEKLY),
            ("per_sprint_day", MeetingFrequency.PER_SPRINT_DAY),
            ("monthly", MeetingFrequency.MONTHLY),
        ],
    )
    def test_parse_from_string(
        self,
        value: str,
        member: MeetingFrequency,
    ) -> None:
        assert MeetingFrequency(value) == member

    def test_invalid_string_raises(self) -> None:
        with pytest.raises(ValueError, match="not_a_frequency"):
            MeetingFrequency("not_a_frequency")


@pytest.mark.unit
class TestFrequencyToSeconds:
    """Tests for frequency_to_seconds conversion."""

    @pytest.mark.parametrize(
        ("freq", "expected"),
        [
            (MeetingFrequency.DAILY, 86_400.0),
            (MeetingFrequency.WEEKLY, 604_800.0),
            (MeetingFrequency.BI_WEEKLY, 1_209_600.0),
            (MeetingFrequency.PER_SPRINT_DAY, 86_400.0),
            (MeetingFrequency.MONTHLY, 2_592_000.0),
        ],
    )
    def test_returns_correct_interval(
        self,
        freq: MeetingFrequency,
        expected: float,
    ) -> None:
        assert frequency_to_seconds(freq) == expected
