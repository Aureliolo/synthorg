"""Tests for the shared ``Clock`` protocol, ``SystemClock``, and ``FakeClock``."""

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest

from synthorg.core.clock import Clock, SystemClock
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


class TestSystemClock:
    """Behavioural tests for the production wall-clock implementation."""

    async def test_now_returns_aware_utc(self) -> None:
        moment = SystemClock().now()
        assert moment.tzinfo is not None
        offset = moment.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0.0

    async def test_monotonic_is_non_decreasing(self) -> None:
        clock = SystemClock()
        a = clock.monotonic()
        b = clock.monotonic()
        assert b >= a

    async def test_sleep_delegates_to_asyncio_sleep(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            calls.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        await SystemClock().sleep(0.05)
        assert calls == [0.05]

    async def test_sleep_zero_still_delegates(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            calls.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        await SystemClock().sleep(0.0)
        assert calls == [0.0]

    async def test_sleep_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            await SystemClock().sleep(-1.0)

    async def test_satisfies_clock_protocol(self) -> None:
        assert isinstance(SystemClock(), Clock)


class TestFakeClock:
    """Sanity checks for the test helper."""

    async def test_sleep_advances_without_waiting(self) -> None:
        clock = FakeClock()
        started = clock.now()
        await clock.sleep(3600.0)
        elapsed = (clock.now() - started).total_seconds()
        assert elapsed == pytest.approx(3600.0)

    async def test_advance_without_recording(self) -> None:
        clock = FakeClock()
        clock.advance(60.0)
        assert clock.sleep_calls == ()

    async def test_satisfies_clock_protocol(self) -> None:
        assert isinstance(FakeClock(), Clock)

    async def test_sleep_records_each_call(self) -> None:
        clock = FakeClock()
        await clock.sleep(1.0)
        await clock.sleep(2.5)
        assert clock.sleep_calls == (1.0, 2.5)

    async def test_now_is_aware_utc(self) -> None:
        moment = FakeClock().now()
        assert moment.tzinfo is UTC

    async def test_monotonic_starts_at_zero(self) -> None:
        clock = FakeClock()
        assert clock.monotonic() == pytest.approx(0.0)

    async def test_monotonic_advances_with_sleep(self) -> None:
        clock = FakeClock()
        await clock.sleep(2.5)
        assert clock.monotonic() == pytest.approx(2.5)

    async def test_monotonic_advances_with_advance(self) -> None:
        clock = FakeClock()
        clock.advance(7.5)
        assert clock.monotonic() == pytest.approx(7.5)

    async def test_monotonic_non_decreasing_across_mixed_ops(self) -> None:
        """``sleep`` + ``advance`` interleaved keep ``monotonic()`` non-decreasing."""
        clock = FakeClock()
        readings: list[float] = [clock.monotonic()]
        await clock.sleep(0.5)
        readings.append(clock.monotonic())
        clock.advance(1.5)
        readings.append(clock.monotonic())
        await clock.sleep(0.0)
        readings.append(clock.monotonic())
        assert readings == sorted(readings)

    async def test_advance_async_yields_to_event_loop(self) -> None:
        """``advance_async`` runs cooperative tasks waiting on the loop."""
        clock = FakeClock()
        observed_after_advance: list[float] = []

        async def watcher() -> None:
            await asyncio.sleep(0)
            observed_after_advance.append(clock.monotonic())

        task = asyncio.create_task(watcher())
        await clock.advance_async(3.0)
        await task
        assert observed_after_advance == [pytest.approx(3.0)]

    async def test_sleep_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            await FakeClock().sleep(-1.0)

    async def test_advance_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            FakeClock().advance(-1.0)

    async def test_custom_start_must_be_aware(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            # The naive datetime here is intentional: the assertion is
            # that ``FakeClock`` rejects it.
            FakeClock(start=datetime(2026, 1, 1))  # noqa: DTZ001

    async def test_custom_start_normalises_to_utc(self) -> None:
        # Use a non-UTC fixed-offset timezone so the assertion below
        # actually exercises the conversion path; if FakeClock returned
        # the input unchanged, the comparison would still pass against
        # a UTC start, hiding a normalisation regression.
        custom_tz = timezone(timedelta(hours=5))
        clock = FakeClock(start=datetime(2030, 6, 1, 12, 0, tzinfo=custom_tz))
        # 12:00 in UTC+5 is 07:00 UTC; the round-trip must produce the
        # equivalent UTC instant, not the same wall-clock numbers.
        assert clock.now() == datetime(2030, 6, 1, 7, 0, tzinfo=UTC)
        # Sanity: epoch starts at the normalised value, so monotonic = 0.
        assert clock.monotonic() == pytest.approx(0.0)
