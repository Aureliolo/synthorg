"""Unit tests for webhook replay protection.

All tests use an injected clock so the nonce / timestamp checks
are fully deterministic -- no dependency on wall-clock time.
"""

import pytest

from synthorg.integrations.webhooks.replay_protection import ReplayProtector


class _FakeClock:
    """Injectable clock for deterministic replay tests."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.unit
class TestReplayProtector:
    """Tests for nonce + timestamp replay protection."""

    @pytest.mark.parametrize(
        ("first_nonce", "second_nonce", "expected_second"),
        [
            ("abc", "abc", False),  # duplicate rejected
            ("a", "b", True),  # distinct accepted
            (None, "abc", True),  # None nonce first, then real
        ],
    )
    def test_nonce_check_matrix(
        self,
        first_nonce: str | None,
        second_nonce: str,
        *,
        expected_second: bool,
    ) -> None:
        clock = _FakeClock()
        protector = ReplayProtector(window_seconds=300, clock=clock)
        protector.check(nonce=first_nonce, timestamp=clock.now)
        assert (
            protector.check(nonce=second_nonce, timestamp=clock.now) is expected_second
        )

    def test_fresh_request_accepted(self) -> None:
        clock = _FakeClock()
        protector = ReplayProtector(window_seconds=300, clock=clock)
        assert protector.check(nonce="abc", timestamp=clock.now) is True

    @pytest.mark.parametrize(
        "skew_seconds",
        [-120, 120],
        ids=["old_timestamp", "future_timestamp"],
    )
    def test_timestamp_outside_window_rejected(
        self,
        skew_seconds: int,
    ) -> None:
        clock = _FakeClock()
        protector = ReplayProtector(window_seconds=60, clock=clock)
        skewed = clock.now + skew_seconds
        assert protector.check(nonce="n", timestamp=skewed) is False

    def test_none_timestamp_with_nonce_accepted(self) -> None:
        clock = _FakeClock()
        protector = ReplayProtector(window_seconds=300, clock=clock)
        assert protector.check(nonce="z", timestamp=None) is True

    def test_both_none_fails_closed(self) -> None:
        """Missing both nonce and timestamp must reject the request."""
        clock = _FakeClock()
        protector = ReplayProtector(window_seconds=300, clock=clock)
        assert protector.check(nonce=None, timestamp=None) is False

    def test_eviction_removes_old_nonces(self) -> None:
        from synthorg.integrations.webhooks.replay_protection import (
            _fingerprint_nonce,
        )

        clock = _FakeClock()
        protector = ReplayProtector(window_seconds=1, clock=clock)
        protector.check(nonce="old", timestamp=clock.now)
        old_key = _fingerprint_nonce("old")
        assert old_key in protector._seen
        clock.advance(10)
        # Trigger eviction via a fresh check.
        protector.check(nonce="new", timestamp=clock.now)
        assert old_key not in protector._seen

    def test_bounded_cache_evicts_oldest(self) -> None:
        """With max_entries reached, oldest nonces are dropped."""
        from synthorg.integrations.webhooks.replay_protection import (
            _fingerprint_nonce,
        )

        clock = _FakeClock()
        protector = ReplayProtector(
            window_seconds=3600,
            max_entries=3,
            clock=clock,
        )
        for i in range(5):
            assert protector.check(nonce=f"n{i}", timestamp=clock.now) is True
        # Only the 3 most recent nonces should remain -- compare
        # against the SHA-256 fingerprints since the cache stores
        # fixed-size digests, not raw attacker-controlled strings.
        assert set(protector._seen) == {
            _fingerprint_nonce("n2"),
            _fingerprint_nonce("n3"),
            _fingerprint_nonce("n4"),
        }

    def test_oversized_nonce_rejected(self) -> None:
        """Nonces over the max-size limit must be rejected outright.

        Protects against an attacker trying to exhaust memory or
        CPU by sending arbitrarily long nonces that would still
        get hashed.
        """
        from synthorg.integrations.webhooks.replay_protection import (
            MAX_NONCE_CHARS,
        )

        clock = _FakeClock()
        protector = ReplayProtector(window_seconds=300, clock=clock)
        big_nonce = "a" * (MAX_NONCE_CHARS + 1)
        assert protector.check(nonce=big_nonce, timestamp=clock.now) is False
        assert not protector._seen

    def test_duplicate_detected_after_single_check(self) -> None:
        """A second call with the same nonce must be rejected."""
        clock = _FakeClock()
        protector = ReplayProtector(window_seconds=300, clock=clock)
        assert protector.check(nonce="once", timestamp=clock.now) is True
        # Advance a little but stay inside the window.
        clock.advance(30)
        assert protector.check(nonce="once", timestamp=clock.now) is False
