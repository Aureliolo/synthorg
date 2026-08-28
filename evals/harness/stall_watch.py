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
import math
from collections.abc import AsyncIterator, Callable
from typing import Final, override

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.tracker import CostTracker
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.evals import (
    EVALS_HARNESS_CELL_STALLED,
    EVALS_HARNESS_STALL_REPORT_FAILED,
)

logger = get_logger(__name__)

#: How often the watch samples idle time, as a fraction of the reporting
#: threshold. Sampling at the threshold itself would report anywhere between one
#: and two thresholds after the fact.
_POLLS_PER_INTERVAL: Final[int] = 10

#: Default idle time before a session is called stalled.
#:
#: Read off what a healthy run was measured doing, and bounded from below by a
#: product ceiling rather than by taste. An idle gap holds at most one tool call
#: and one model call, and ``tools.shell_command_timeout_seconds`` caps the
#: first at 600s whatever an agent asks for (``ShellCommandArgs.timeout`` is
#: ``le=600``), so a gap past 600s is structurally not a running test suite. The
#: first recursion-depth recording bears that out: at 300s, 39 sessions were
#: reported stalled and every one of them resumed, the longest after 603s, which
#: is one command running its whole ceiling. A notification that fires on every
#: healthy deep leaf is one an operator learns to skip, so this sits at twice
#: the measured healthy maximum, which no command-then-call chain reaches.
DEFAULT_STALL_IDLE_SECONDS: Final[float] = 1200.0


class ProgressTrackingLedger(CostTracker):
    """A cost ledger that remembers when it was last written to.

    Subclasses the real tracker rather than wrapping it: the gateway records
    through whatever tracker the application state carries, and a wrapper that
    forwarded some calls and not others would leave the scoreboard's spend
    column short by whatever it forgot.

    Progress is remembered per TASK as well as for the ledger as a whole,
    because one of these is installed per cell and a cell's sessions run
    concurrently. Read whole, a session that has stopped answering is invisible
    for as long as any sibling is working, which is the one thing a stall watch
    exists to see.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        super().__init__()
        self._progress_clock: Clock = clock if clock is not None else SystemClock()
        self._last_progress: float = self._progress_clock.monotonic()
        self._opened_at: float = self._last_progress
        self._last_by_task: dict[str, float] = {}

    @override
    async def record(self, cost_record: CostRecord) -> None:
        """Store *cost_record* and mark its owner as having made progress."""
        now = self._progress_clock.monotonic()
        self._last_progress = now
        if cost_record.task_id is not None:
            self._last_by_task[str(cost_record.task_id)] = now
        await super().record(cost_record)

    def open_task(self, task_id: str) -> None:
        """Start *task_id*'s idle clock now, before it has dispatched.

        The ledger is built once per CELL and a cell runs for hours, so a
        session opening late (a merge, or a leaf in a later concurrency wave)
        would otherwise read its idle time from the cell's own start and be
        reported stalled on its first poll while working normally. Its clock
        starts when somebody begins watching it instead.

        Idempotent: a task that has already recorded keeps the reading it
        earned, so re-opening a watch cannot hide a genuine stall.

        Args:
            task_id: The task a watch is about to follow.
        """
        self._last_by_task.setdefault(task_id, self._progress_clock.monotonic())

    def idle_seconds(self, *, task_id: str | None = None) -> float:
        """Seconds since the last dispatch this ledger saw for *task_id*.

        Args:
            task_id: Whose progress is wanted, or ``None`` for the ledger as a
                whole. A task neither watched nor dispatched falls back to the
                ledger's construction, which is what the whole-ledger reading
                already means for a ledger nothing has written to.

        Returns:
            The idle duration.
        """
        since = (
            self._last_progress
            if task_id is None
            else self._last_by_task.get(task_id, self._opened_at)
        )
        return self._progress_clock.monotonic() - since


class StallWatch:
    """Reports a session that has gone quiet, for as long as it stays quiet.

    Args:
        ledger: The cost ledger progress is read from. Shared with every other
            session of the same cell, which is why *task_id* exists.
        cell: Identifier of what is being watched, for the report.
        idle_seconds: Idle time at which it is called stalled.
        notify: Called with the observed idle time on each report. Separate
            from the log line so a caller driving a long unattended matrix can
            put the same fact somewhere it will actually be seen.
        task_id: Whose progress decides this watch, or ``None`` to read the
            whole ledger. Named rather than inferred from *cell*, because the
            two are different identifiers: a cell label names one session and
            the ledger keys its records by task.
        clock: Time source; tests inject a fake.

    Raises:
        ValueError: *idle_seconds* is not finite and positive.
    """

    def __init__(
        self,
        *,
        ledger: ProgressTrackingLedger,
        cell: NotBlankStr,
        idle_seconds: float = DEFAULT_STALL_IDLE_SECONDS,
        notify: Callable[[float], None],
        task_id: NotBlankStr | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not math.isfinite(idle_seconds) or idle_seconds <= 0:
            # A zero or negative interval polls with no sleep and reports on
            # every pass, so the watch that exists to surface a wedged cell
            # becomes the thing burying it.
            msg = f"idle_seconds must be finite and positive, got {idle_seconds!r}"
            raise ValueError(msg)
        self._ledger = ledger
        self._cell = cell
        self._idle_seconds = idle_seconds
        self._notify = notify
        self._task_id = task_id
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._reported_at: float = 0.0
        self._task: asyncio.Task[None] | None = None
        if task_id is not None:
            # Opening the watch is what starts this task's clock. Without it a
            # session joining a hours-old cell ledger reads the cell's whole
            # elapsed time as its own idle and reports a stall immediately.
            ledger.open_task(str(task_id))

    def observed_idle(self) -> float:
        """The idle time this watch reads, which is its own task's.

        Returns:
            The idle duration.
        """
        return self._ledger.idle_seconds(task_id=self._task_id)

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
                await self._settle(task)

    async def _settle(self, task: asyncio.Task[None]) -> None:
        """Await the cancelled poller without letting it speak for the block.

        This runs in a ``finally``, so a poller failure re-raised here would
        replace whatever the watched block was doing, including a measurement
        that had already succeeded. The watch is a notification channel; it
        reports its own failure and never becomes the run's outcome.
        """
        try:
            await task
        except asyncio.CancelledError:
            return
        except MemoryError, RecursionError:
            raise
        except Exception as exc:  # noqa: BLE001 -- reported, never fatal
            logger.warning(
                EVALS_HARNESS_STALL_REPORT_FAILED,
                cell=self._cell,
                phase="poller",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _poll(self) -> None:
        """Sample idle time until cancelled, reporting each fresh stall."""
        interval = self._idle_seconds / _POLLS_PER_INTERVAL
        while True:
            await self._clock.sleep(interval)
            self._report_if_stalled()

    def _report_if_stalled(self) -> None:
        """Report this poll's idle time, if it is a fresh stall.

        The notifier is the caller's, so it can fail. It must not take the
        watch down with it: a poller that died on one notification stops
        reporting for the rest of a cell that may run for hours, which is the
        one thing this exists to prevent.
        """
        idle = self.observed_idle()
        if not self.should_report(idle):
            return
        logger.warning(
            EVALS_HARNESS_CELL_STALLED,
            cell=self._cell,
            task_id=self._task_id,
            idle_seconds=idle,
            threshold_seconds=self._idle_seconds,
            note=(
                "no LLM call has completed for this task in that long; the "
                "run is not being stopped"
            ),
        )
        try:
            self._notify(idle)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:  # noqa: BLE001 -- reported, never fatal
            logger.warning(
                EVALS_HARNESS_STALL_REPORT_FAILED,
                cell=self._cell,
                phase="notify",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


__all__ = [
    "DEFAULT_STALL_IDLE_SECONDS",
    "ProgressTrackingLedger",
    "StallWatch",
]
