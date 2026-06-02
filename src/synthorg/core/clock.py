"""Clock abstraction for the SynthOrg codebase.

A single dependency-injection seam over wall-clock and monotonic time
plus cooperative async sleeping. Subsystems that read time (rate
limiters, webhook replay protection, cache TTLs, health probers,
schedulers) accept a ``Clock`` parameter so tests can substitute a
``FakeClock`` and advance virtual time deterministically.

The protocol exposes three orthogonal time sources because the codebase
genuinely needs all three:

- ``now()`` returns the current UTC-aware wall-clock time. Used for
  audit timestamps, persisted records, and any value the operator
  inspects.
- ``monotonic()`` returns a non-decreasing seconds count from an
  arbitrary epoch. Used for rate-limit windows, deadlines, and any
  computation that must survive a system clock change.
- ``sleep(seconds)`` is a cooperative async sleep. Used by retry loops
  and grace timers so tests can advance time without real waiting.

``SystemClock`` is the production implementation; tests inject the
``FakeClock`` from ``tests/_shared/fake_clock.py``.

Coexisting plain-callable seam
------------------------------

A handful of communication-side modules accept a ``clock:
Callable[[], float] = time.monotonic`` parameter rather than a full
``Clock`` protocol value:

- ``src/synthorg/communication/loop_prevention/circuit_breaker.py``
- ``src/synthorg/communication/loop_prevention/dedup.py``
- ``src/synthorg/communication/loop_prevention/rate_limit.py``
- ``src/synthorg/communication/meeting/scheduler.py``

Their time use is narrow (monotonic ticks only; no UTC wall-clock,
no async sleep) so the protocol's three orthogonal sources buy
them nothing. Tests inject deterministic callables directly; the
``Clock`` protocol and the plain-callable parameter coexist as
equivalent deterministic-time injection seams for their respective
scopes.
"""

import asyncio
import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Injectable time source.

    Implementations MUST satisfy:

    - ``now()`` returns a timezone-aware UTC datetime.
    - ``monotonic()`` returns a non-decreasing float, in seconds, from
      an arbitrary epoch fixed at construction.
    - ``sleep(seconds)`` rejects negative durations with ``ValueError``
      so a buggy caller cannot silently sleep zero.
    """

    def now(self) -> datetime:
        """Return the current UTC-aware wall-clock time."""
        ...

    def monotonic(self) -> float:
        """Return seconds since an arbitrary epoch; non-decreasing."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Suspend the caller for approximately ``seconds``.

        Args:
            seconds: Non-negative duration in seconds.

        Raises:
            ValueError: If ``seconds`` is negative.
        """
        ...


class SystemClock:
    """Production clock backed by ``datetime``, ``time``, ``asyncio``.

    Stateless, safe to instantiate per-class as a default argument.
    """

    def now(self) -> datetime:
        """Return ``datetime.now(UTC)``."""
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Return ``time.monotonic()``."""
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        """Await ``asyncio.sleep`` for ``seconds``.

        Rejects negative ``seconds`` so a bug that computes a negative
        duration surfaces at the boundary instead of silently sleeping
        zero.

        Args:
            seconds: Non-negative duration in seconds.

        Raises:
            ValueError: If ``seconds`` is negative.
        """
        if seconds < 0.0:
            msg = f"sleep seconds must be non-negative, got {seconds}"
            raise ValueError(msg)
        await asyncio.sleep(seconds)
