"""Tests for the shared sync-handler backoff schedule."""

import pytest

from synthorg.observability._sync_backoff import backoff_delay


@pytest.mark.unit
class TestBackoffDelay:
    """The bounded exponential schedule both sync log sinks share."""

    @pytest.mark.parametrize(
        ("attempt", "expected"),
        [
            (0, 0.5),
            (1, 1.0),
            (2, 2.0),
            (3, 4.0),
            (4, 8.0),
        ],
    )
    def test_doubles_until_cap(self, attempt: int, expected: float) -> None:
        assert backoff_delay(attempt) == expected

    def test_caps_at_eight_seconds(self) -> None:
        # Beyond attempt 4 the raw delay exceeds the cap and is clamped.
        assert backoff_delay(5) == 8.0
        assert backoff_delay(10) == 8.0
