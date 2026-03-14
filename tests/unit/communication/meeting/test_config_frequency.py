"""Tests for MeetingTypeConfig frequency type change."""

import pytest
from pydantic import ValidationError

from synthorg.communication.config import MeetingTypeConfig
from synthorg.communication.meeting.frequency import MeetingFrequency


@pytest.mark.unit
class TestMeetingTypeConfigFrequency:
    """Tests for MeetingFrequency enum in MeetingTypeConfig."""

    def test_accepts_enum_value(self) -> None:
        """MeetingTypeConfig accepts MeetingFrequency enum for frequency field."""
        config = MeetingTypeConfig(
            name="standup",
            frequency=MeetingFrequency.DAILY,
            participants=("engineering",),
        )
        assert config.frequency == MeetingFrequency.DAILY

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("daily", MeetingFrequency.DAILY),
            ("weekly", MeetingFrequency.WEEKLY),
            ("bi_weekly", MeetingFrequency.BI_WEEKLY),
            ("per_sprint_day", MeetingFrequency.PER_SPRINT_DAY),
            ("monthly", MeetingFrequency.MONTHLY),
        ],
    )
    def test_string_coercion(
        self,
        raw: str,
        expected: MeetingFrequency,
    ) -> None:
        """Pydantic coerces raw strings from YAML config."""
        config = MeetingTypeConfig(
            name="test",
            frequency=raw,  # type: ignore[arg-type]
            participants=("engineering",),
        )
        assert config.frequency == expected

    def test_exactly_one_of_frequency_or_trigger(self) -> None:
        """Validation: both set raises."""
        with pytest.raises(ValidationError, match="Only one of"):
            MeetingTypeConfig(
                name="bad",
                frequency=MeetingFrequency.DAILY,
                trigger="some_event",
                participants=("engineering",),
            )

    def test_neither_set_raises(self) -> None:
        """Validation: neither set raises."""
        with pytest.raises(ValidationError, match="Exactly one of"):
            MeetingTypeConfig(
                name="bad",
                participants=("engineering",),
            )

    def test_trigger_still_works(self) -> None:
        """Trigger-based config still works unchanged."""
        config = MeetingTypeConfig(
            name="review",
            trigger="code_review_complete",
            participants=("engineering",),
        )
        assert config.trigger == "code_review_complete"
        assert config.frequency is None
