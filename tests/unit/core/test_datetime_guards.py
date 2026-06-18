"""Tests for the datetime range and window validation guards."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.datetime_guards import (
    validate_datetime_range,
    validate_time_window,
)

_EARLY = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
_LATE = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


@pytest.mark.unit
class TestValidateDatetimeRange:
    """``validate_datetime_range`` only rejects an inverted full range."""

    def test_ordered_range_passes(self) -> None:
        validate_datetime_range(_EARLY, _LATE)

    def test_open_start_passes(self) -> None:
        validate_datetime_range(None, _LATE)

    def test_open_end_passes(self) -> None:
        validate_datetime_range(_EARLY, None)

    def test_both_none_passes(self) -> None:
        validate_datetime_range(None, None)

    def test_inverted_range_raises(self) -> None:
        with pytest.raises(ValueError, match="must be before"):
            validate_datetime_range(_LATE, _EARLY)

    def test_equal_bounds_raise(self) -> None:
        with pytest.raises(ValueError, match="must be before"):
            validate_datetime_range(_EARLY, _EARLY)

    def test_custom_labels_in_message(self) -> None:
        with pytest.raises(ValueError, match=r"from .* must be before to"):
            validate_datetime_range(_LATE, _EARLY, start_label="from", end_label="to")


@pytest.mark.unit
class TestValidateTimeWindow:
    """``validate_time_window`` requires aware, strictly-ordered bounds."""

    def test_ordered_window_passes(self) -> None:
        validate_time_window(_EARLY, _LATE)

    def test_naive_since_raises(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            validate_time_window(_EARLY.replace(tzinfo=None), _LATE)

    def test_naive_until_raises(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            validate_time_window(_EARLY, _LATE.replace(tzinfo=None))

    def test_inverted_window_raises(self) -> None:
        with pytest.raises(ValueError, match="must be earlier than until"):
            validate_time_window(_LATE, _EARLY)

    def test_empty_window_raises(self) -> None:
        with pytest.raises(ValueError, match="must be earlier than until"):
            validate_time_window(_EARLY, _EARLY)

    def test_offset_window_passes(self) -> None:
        offset = datetime(2026, 6, 15, 9, 0, tzinfo=UTC) + timedelta(hours=1)
        validate_time_window(offset, _LATE)
