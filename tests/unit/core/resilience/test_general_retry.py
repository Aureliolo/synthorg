"""Tests for :class:`synthorg.core.resilience.GeneralRetryHandler`."""

import pytest
import structlog.testing

from synthorg.core.resilience import GeneralRetryHandler
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


def _always_retryable(_exc: BaseException) -> bool:
    return True


def _never_retryable(_exc: BaseException) -> bool:
    return False


class TestGeneralRetryHandler:
    """Behavioural tests for the general retry helper."""

    async def test_first_attempt_succeeds(self) -> None:
        calls = 0

        async def op() -> str:
            nonlocal calls
            calls += 1
            return "ok"

        handler = GeneralRetryHandler(
            retryable=_always_retryable,
            max_attempts=3,
            base=0.0,
            cap=0.0,
            event="test.retry",
        )
        result = await handler.execute(op)

        assert result == "ok"
        assert calls == 1

    async def test_retries_then_succeeds(self) -> None:
        # FakeClock-first: ``GeneralRetryHandler`` accepts ``clock=``,
        # so we inject a fake instead of monkeypatching ``asyncio.sleep``
        # globally.  ``fake_clock.sleep_calls`` records every requested
        # delay for assertion.
        clock = FakeClock()

        calls = 0

        async def op() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                msg = "transient"
                raise RuntimeError(msg)
            return "ok"

        handler = GeneralRetryHandler(
            retryable=_always_retryable,
            max_attempts=5,
            base=0.1,
            cap=10.0,
            event="test.retry",
            jitter=False,
            clock=clock,
        )
        with structlog.testing.capture_logs() as events:
            result = await handler.execute(op, source="test")

        assert result == "ok"
        assert calls == 3
        assert len(clock.sleep_calls) == 2  # one before each retry
        retry_events = [e for e in events if e.get("event") == "test.retry"]
        assert len(retry_events) == 2
        for e in retry_events:
            assert e.get("log_level") == "warning"
            assert e.get("error_type") == "RuntimeError"
            assert e.get("source") == "test"
            assert e.get("max_attempts") == 5

    async def test_raises_after_max_attempts(self) -> None:
        clock = FakeClock()
        boom = "boom"

        async def op() -> str:
            raise RuntimeError(boom)

        handler = GeneralRetryHandler(
            retryable=_always_retryable,
            max_attempts=3,
            base=0.0,
            cap=0.0,
            event="test.retry",
            clock=clock,
        )

        with pytest.raises(RuntimeError, match="boom"):
            await handler.execute(op)

    async def test_non_retryable_propagates_immediately(self) -> None:
        calls = 0

        invalid = "invalid"

        async def op() -> str:
            nonlocal calls
            calls += 1
            raise ValueError(invalid)

        handler = GeneralRetryHandler(
            retryable=_never_retryable,
            max_attempts=5,
            base=0.0,
            cap=0.0,
            event="test.retry",
        )

        with pytest.raises(ValueError, match="invalid"):
            await handler.execute(op)

        assert calls == 1

    async def test_predicate_filters_specific_exception(self) -> None:
        non_retryable = "non-retryable"

        async def op() -> str:
            raise TypeError(non_retryable)

        handler = GeneralRetryHandler(
            retryable=lambda exc: isinstance(exc, RuntimeError),
            max_attempts=5,
            base=0.0,
            cap=0.0,
            event="test.retry",
        )

        with pytest.raises(TypeError):
            await handler.execute(op)

    async def test_zero_base_skips_sleep(self) -> None:
        clock = FakeClock()
        calls = 0
        transient = "transient"

        async def op() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise RuntimeError(transient)
            return "ok"

        handler = GeneralRetryHandler(
            retryable=_always_retryable,
            max_attempts=3,
            base=0.0,
            cap=0.0,
            event="test.retry",
            clock=clock,
        )
        await handler.execute(op)

        # ``base=0`` short-circuits the ``await self._clock.sleep(...)``
        # call site (delay > 0 gate); FakeClock should record nothing.
        assert clock.sleep_calls == ()
        # Verify the delay computation itself returns exactly 0.0 so
        # a future regression that returns a small epsilon (e.g.
        # ``return self._base or 1e-9``) is caught.
        assert handler._compute_delay(0) == 0.0
        assert handler._compute_delay(5) == 0.0

    async def test_jitter_with_base_zero_returns_zero(self) -> None:
        # Even when jitter=True, base=0 must short-circuit to 0.0
        # before random sampling so self-correction loops don't
        # accidentally introduce sleep.
        handler = GeneralRetryHandler(
            retryable=_always_retryable,
            max_attempts=3,
            base=0.0,
            cap=0.0,
            event="test.retry",
            jitter=True,
        )
        assert handler._compute_delay(0) == 0.0
        assert handler._compute_delay(3) == 0.0

    async def test_delay_caps_at_cap_no_jitter(self) -> None:
        clock = FakeClock()
        calls = 0
        transient = "transient"

        async def op() -> str:
            nonlocal calls
            calls += 1
            if calls < 5:
                raise RuntimeError(transient)
            return "ok"

        handler = GeneralRetryHandler(
            retryable=_always_retryable,
            max_attempts=5,
            base=10.0,
            cap=15.0,
            event="test.retry",
            jitter=False,
            clock=clock,
        )
        await handler.execute(op)

        # base=10, cap=15.  Delays: attempt0 -> 10, attempt1 -> 15
        # (capped from 20), attempt2 -> 15, attempt3 -> 15.
        sleeps = clock.sleep_calls
        assert all(s <= 15.0 for s in sleeps)
        assert sleeps[0] == 10.0
        assert sleeps[1] == 15.0

    async def test_jitter_returns_value_in_range(self) -> None:
        clock = FakeClock()
        calls = 0
        transient = "transient"

        async def op() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError(transient)
            return "ok"

        handler = GeneralRetryHandler(
            retryable=_always_retryable,
            max_attempts=5,
            base=1.0,
            cap=10.0,
            event="test.retry",
            jitter=True,
            clock=clock,
        )
        await handler.execute(op)

        assert all(0 <= s <= 2.0 for s in clock.sleep_calls)

    async def test_max_attempts_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_attempts must be >= 1"):
            GeneralRetryHandler(
                retryable=_always_retryable,
                max_attempts=0,
                base=0.1,
                cap=1.0,
                event="test",
            )

    async def test_base_must_be_non_negative(self) -> None:
        with pytest.raises(ValueError, match=r"base must be a finite number >= 0"):
            GeneralRetryHandler(
                retryable=_always_retryable,
                max_attempts=3,
                base=-1.0,
                cap=1.0,
                event="test",
            )

    async def test_cap_must_be_at_least_base(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"cap must be a finite number >= base",
        ):
            GeneralRetryHandler(
                retryable=_always_retryable,
                max_attempts=3,
                base=10.0,
                cap=5.0,
                event="test",
            )

    async def test_base_rejects_nan(self) -> None:
        with pytest.raises(ValueError, match=r"base must be a finite"):
            GeneralRetryHandler(
                retryable=_always_retryable,
                max_attempts=3,
                base=float("nan"),
                cap=1.0,
                event="test",
            )

    async def test_cap_rejects_inf(self) -> None:
        with pytest.raises(ValueError, match=r"cap must be a finite"):
            GeneralRetryHandler(
                retryable=_always_retryable,
                max_attempts=3,
                base=0.1,
                cap=float("inf"),
                event="test",
            )

    async def test_log_ctx_propagated_to_event(self) -> None:
        clock = FakeClock()
        calls = 0
        transient = "transient"

        async def op() -> str:
            nonlocal calls
            calls += 1
            if calls < 2:
                raise RuntimeError(transient)
            return "ok"

        handler = GeneralRetryHandler(
            retryable=_always_retryable,
            max_attempts=3,
            base=0.1,
            cap=1.0,
            event="my.event",
            jitter=False,
            clock=clock,
        )

        with structlog.testing.capture_logs() as events:
            await handler.execute(op, task_id="t1", endpoint="/x")

        retry_events = [e for e in events if e.get("event") == "my.event"]
        assert len(retry_events) == 1
        assert retry_events[0].get("task_id") == "t1"
        assert retry_events[0].get("endpoint") == "/x"
