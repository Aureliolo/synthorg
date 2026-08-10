# module-kind: code
"""Notice a cell that has stopped making progress, and say so.

A recording against a zero-priced provider has no cost ceiling that can trip:
the gateway's hard kill is keyed on spend, and spend stays at zero however long
the run goes on. What remains is the brief's turn count, which bounds a
*working* run and says nothing about one whose provider stopped answering. Such
a cell sits until somebody looks at it, and on a sequential matrix that strands
every cell behind it.

This watches and reports. It never ends a run, because a cap chosen before the
first measurement ends healthy-but-slow runs as failures, and the rubric would
then score the cap rather than the loop. What a stall should cost is a decision
worth making from evidence.

The tap is the cell's own cost ledger, which every dispatch from both legs
writes through, so no loop needs changing and neither governance boundary is
touched. A long tool call between two LLM calls therefore reads as idle, which
is honest for a notification: the run has produced nothing measurable for that
long, and whether that is fine is exactly the judgement being deferred.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Final

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_LOOP_AB_CELL_STALLED

logger = get_logger(__name__)

#: How often the watch samples idle time, as a fraction of the reporting
#: threshold. Sampling at the threshold itself would report anywhere between one
#: and two thresholds after the fact.
_POLLS_PER_INTERVAL: Final[int] = 10

#: Default idle time before a cell is called stalled.
DEFAULT_STALL_IDLE_SECONDS: Final[float] = 300.0


class ProgressTrackingLedger(CostTracker):
    """A cost ledger that remembers when it was last written to.

    Subclasses the real tracker rather than wrapping it: the gateway records
    through whatever tracker the application state carries, and a wrapper that
    forwarded some calls and not others would leave the scoreboard's spend
    column short by whatever it forgot.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        super().__init__()
        self._progress_clock: Clock = clock if clock is not None else SystemClock()
        self._last_progress: float = self._progress_clock.monotonic()

    async def record(self, cost_record: CostRecord) -> None:
        """Store *cost_record* and mark the cell as having made progress."""
        self._last_progress = self._progress_clock.monotonic()
        await super().record(cost_record)

    def idle_seconds(self) -> float:
        """Seconds since the last dispatch this ledger saw.

        Returns:
            The idle duration, measured from the ledger's construction until
            the first dispatch lands.
        """
        return self._progress_clock.monotonic() - self._last_progress


class StallWatch:
    """Reports a cell that has gone quiet, for as long as it stays quiet.

    Args:
        ledger: The cell's cost ledger, read for its idle time.
        cell: Identifier of the cell being watched, for the report.
        idle_seconds: Idle time at which the cell is called stalled.
        notify: Called with the observed idle time on each report. Separate
            from the log line so a caller driving a long unattended matrix can
            put the same fact somewhere it will actually be seen.
        clock: Time source; tests inject a fake.
    """

    def __init__(
        self,
        *,
        ledger: ProgressTrackingLedger,
        cell: NotBlankStr,
        idle_seconds: float = DEFAULT_STALL_IDLE_SECONDS,
        notify: Callable[[float], None],
        clock: Clock | None = None,
    ) -> None:
        self._ledger = ledger
        self._cell = cell
        self._idle_seconds = idle_seconds
        self._notify = notify
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._reported_at: float = 0.0
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """Whether the watch is currently polling."""
        return self._task is not None and not self._task.done()

    def should_report(self, idle: float) -> bool:
        """Decide whether *idle* is a stall worth reporting now.

        Reports on crossing the threshold and then only once per further whole
        threshold, so a cell wedged for an hour produces a handful of lines
        rather than one per poll. A dispatch resets idle to zero, which rearms
        the report: the next quiet stretch is a new stall, not a continuation.

        Returns:
            Whether to report at this idle time.
        """
        if idle < self._idle_seconds:
            self._reported_at = 0.0
            return False
        if idle - self._reported_at < self._idle_seconds:
            return False
        self._reported_at = idle
        return True

    @contextlib.asynccontextmanager
    async def watching(self) -> AsyncIterator[None]:
        """Poll for the duration of the block, whatever ends it.

        Yields:
            Nothing; the watch runs alongside the block.
        """
        self._task = asyncio.create_task(self._poll(), name=f"stall-watch:{self._cell}")
        try:
            yield
        finally:
            task, self._task = self._task, None
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _poll(self) -> None:
        """Sample idle time until cancelled, reporting each fresh stall."""
        interval = self._idle_seconds / _POLLS_PER_INTERVAL
        while True:
            await self._clock.sleep(interval)
            idle = self._ledger.idle_seconds()
            if not self.should_report(idle):
                continue
            logger.warning(
                EVALS_LOOP_AB_CELL_STALLED,
                cell=self._cell,
                idle_seconds=idle,
                threshold_seconds=self._idle_seconds,
                note=(
                    "no LLM call has completed for this cell in that long; the "
                    "run is not being stopped"
                ),
            )
            self._notify(idle)


__all__ = [
    "DEFAULT_STALL_IDLE_SECONDS",
    "ProgressTrackingLedger",
    "StallWatch",
]
