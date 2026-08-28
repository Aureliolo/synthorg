# module-kind: tests
"""The stall watch: notice a wedged cell, never end one.

Every model this harness records against prices at zero, so the gateway's per-run
cost ceiling can never trip and the only bound left on a cell is its turn count.
A run whose provider stops answering therefore sits until someone looks. The
watch exists to make someone look.

It notifies and nothing else. Deciding what a stall means is a judgement nobody
has the evidence for yet, and a cap chosen in advance ends healthy-but-slow runs
as failures, which is the latency dimension being scored as correctness.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from evals.harness.stall_watch import (
    DEFAULT_STALL_IDLE_SECONDS,
    ProgressTrackingLedger,
    StallWatch,
)
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.types import NotBlankStr
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit

_IDLE_SECONDS = 300.0
_CELL = "loop-ab-react-large-loop-ab-simple-0"


def _record(task_id: str | None = None) -> CostRecord:
    """One dispatch the gateway charged for.

    Args:
        task_id: Whose work it was, or ``None`` for work owning no task.

    Returns:
        A minimal cost record.
    """
    return CostRecord(
        provider=NotBlankStr("example-provider"),
        model=NotBlankStr("example-expert-001"),
        input_tokens=10,
        output_tokens=5,
        cost=0.0,
        currency=DEFAULT_CURRENCY,
        timestamp=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        task_id=NotBlankStr(task_id) if task_id is not None else None,
    )


def _watch(
    ledger: ProgressTrackingLedger,
    clock: FakeClock,
    notify: Callable[[float], None] | None = None,
    task_id: str | None = None,
) -> StallWatch:
    """A watch over *ledger*, notifying into *notify*.

    Returns:
        The configured watch.
    """
    return StallWatch(
        ledger=ledger,
        cell=NotBlankStr(_CELL),
        idle_seconds=_IDLE_SECONDS,
        notify=notify if notify is not None else _ignore,
        clock=clock,
        task_id=NotBlankStr(task_id) if task_id is not None else None,
    )


def _ignore(_idle_seconds: float) -> None:
    """Take a report and do nothing with it."""


class TestProgressTrackingLedger:
    async def test_a_dispatch_counts_as_progress(self) -> None:
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        clock.advance(60.0)

        await ledger.record(_record())

        assert ledger.idle_seconds() == 0.0

    async def test_idle_time_accumulates_while_nothing_dispatches(self) -> None:
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)

        clock.advance(120.0)

        assert ledger.idle_seconds() == 120.0

    async def test_the_record_still_reaches_the_ledger(self) -> None:
        # The tap is on the run's authoritative cost sink, so a wrapper that
        # observed without storing would zero the scoreboard's whole spend
        # column while every stall assertion still passed.
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)

        await ledger.record(_record())

        assert len(await ledger.collect_records()) == 1


class TestOneLedgerHoldsManySessions:
    """A cell's sessions share one ledger, so idle has to be asked per task.

    The sink is installed once per CELL, because installing it per session
    swaps a process-wide field while sibling leaves are mid-flight. That is the
    right shape for spend and the wrong one for progress: read cell-wide, a
    wedged leaf is invisible for as long as any sibling is working, and one
    quiet cell reports once per concurrent watch under a different session
    label each time.
    """

    async def test_another_tasks_dispatch_is_not_this_tasks_progress(self) -> None:
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        clock.advance(400.0)

        await ledger.record(_record("task-a"))

        assert ledger.idle_seconds(task_id="task-a") == 0.0
        assert ledger.idle_seconds(task_id="task-b") == 400.0

    async def test_a_task_that_never_dispatched_reads_from_the_start(self) -> None:
        # The same thing the ledger-wide reading means for a fresh ledger: a
        # session that has not answered yet has been idle since it opened.
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)

        clock.advance(90.0)

        assert ledger.idle_seconds(task_id="task-a") == 90.0

    async def test_no_task_still_reads_the_whole_ledger(self) -> None:
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        await ledger.record(_record("task-a"))

        clock.advance(30.0)

        assert ledger.idle_seconds() == 30.0

    async def test_a_record_owning_no_task_is_still_ledger_progress(self) -> None:
        # `task_id` is None for work no task owns, and that dispatch is still
        # the ledger making progress even though it belongs to no session.
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        clock.advance(60.0)

        await ledger.record(_record())

        assert ledger.idle_seconds() == 0.0
        assert ledger.idle_seconds(task_id="task-a") == 60.0

    async def test_opening_a_watch_starts_its_tasks_clock(self) -> None:
        """A session joining an hours-old cell ledger is not already stalled.

        The ledger is built once per CELL, so a merge or a later-wave leaf
        opens against a clock that has been running for hours. Measured from
        the ledger's construction its first poll crosses the threshold at
        once and reports a stall for a session that is working normally.
        """
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        clock.advance(7200.0)

        watch = _watch(ledger, clock, task_id="task-late")

        assert watch.observed_idle() == 0.0

    async def test_opening_a_watch_never_hides_an_existing_stall(self) -> None:
        """Idempotent, so re-watching cannot reset a clock that is running."""
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        await ledger.record(_record("task-c"))
        clock.advance(900.0)

        watch = _watch(ledger, clock, task_id="task-c")

        assert watch.observed_idle() == 900.0

    async def test_the_watch_reads_its_own_tasks_idle(self) -> None:
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        watch = _watch(ledger, clock, task_id="task-b")
        clock.advance(_IDLE_SECONDS)
        await ledger.record(_record("task-a"))

        assert watch.observed_idle() >= _IDLE_SECONDS


class TestTheDefaultThresholdIsSetFromEvidence:
    """A threshold below what a healthy run does is a notification nobody reads.

    An idle gap is bounded from below by what one tool call may occupy, and
    `tools.shell_command_timeout_seconds` caps that at 600s whatever an agent
    asks for. So a gap past 600s is structurally not a running test suite, and
    the first recording bears that out: 39 sessions crossed the old 300s
    threshold and resumed, the longest at 603s. The default sits at twice that,
    which no legitimate command-then-call chain reaches.
    """

    def test_it_is_past_what_a_healthy_run_was_measured_doing(self) -> None:
        assert DEFAULT_STALL_IDLE_SECONDS == 1200.0

    def test_a_watch_built_without_one_uses_it(self) -> None:
        watch = StallWatch(
            ledger=ProgressTrackingLedger(clock=FakeClock()),
            cell=NotBlankStr(_CELL),
            notify=lambda _idle: None,
        )

        assert watch.should_report(DEFAULT_STALL_IDLE_SECONDS - 1.0) is False
        assert watch.should_report(DEFAULT_STALL_IDLE_SECONDS) is True


class TestWhenToReport:
    def test_below_the_threshold_says_nothing(self) -> None:
        clock = FakeClock()
        watch = _watch(ProgressTrackingLedger(clock=clock), clock)

        assert watch.should_report(_IDLE_SECONDS - 1.0) is False

    def test_crossing_the_threshold_reports(self) -> None:
        clock = FakeClock()
        watch = _watch(ProgressTrackingLedger(clock=clock), clock)

        assert watch.should_report(_IDLE_SECONDS) is True

    def test_a_still_stalled_cell_is_not_re_reported_every_poll(self) -> None:
        # Polling is a fraction of the threshold, so a cell wedged for an hour
        # would otherwise emit a line every few seconds and bury the run's own
        # output in its own alarm.
        clock = FakeClock()
        watch = _watch(ProgressTrackingLedger(clock=clock), clock)
        watch.should_report(_IDLE_SECONDS)

        assert watch.should_report(_IDLE_SECONDS + 1.0) is False

    def test_a_cell_stalled_another_whole_interval_reports_again(self) -> None:
        clock = FakeClock()
        watch = _watch(ProgressTrackingLedger(clock=clock), clock)
        watch.should_report(_IDLE_SECONDS)

        assert watch.should_report(_IDLE_SECONDS * 2) is True

    def test_progress_rearms_the_report(self) -> None:
        # Idle resets to zero on a dispatch, so the next stall is a new one and
        # has to be reported as such rather than swallowed by the last.
        clock = FakeClock()
        watch = _watch(ProgressTrackingLedger(clock=clock), clock)
        watch.should_report(_IDLE_SECONDS)
        watch.should_report(0.0)

        assert watch.should_report(_IDLE_SECONDS) is True


class TestWatching:
    async def test_an_idle_cell_is_reported(self) -> None:
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        seen: list[float] = []
        reported = asyncio.Event()

        def _notify(idle_seconds: float) -> None:
            seen.append(idle_seconds)
            reported.set()

        # The watch's own ``clock.sleep`` advances virtual time, so nothing here
        # has to wait: the poll loop walks the clock to the threshold itself.
        async with _watch(ledger, clock, _notify).watching():
            await asyncio.wait_for(reported.wait(), timeout=5.0)

        assert seen
        assert seen[0] >= _IDLE_SECONDS

    async def test_the_watch_stops_with_the_cell(self) -> None:
        # A watch left running would outlive the ledger it reads and go on
        # reporting a cell that finished, on every remaining cell of the matrix.
        clock = FakeClock()
        watch = _watch(ProgressTrackingLedger(clock=clock), clock)

        async with watch.watching():
            assert watch.is_running

        assert not watch.is_running

    async def test_a_raising_cell_still_stops_the_watch(self) -> None:
        clock = FakeClock()
        watch = _watch(ProgressTrackingLedger(clock=clock), clock)
        failure = RuntimeError("the cell failed mid-run")

        with pytest.raises(RuntimeError):
            async with watch.watching():
                raise failure

        assert not watch.is_running

    async def test_a_raising_notifier_does_not_stop_the_watch(self) -> None:
        # The notifier is the caller's, so it can fail. A poller that died on
        # one notification would stop reporting for the rest of a cell that may
        # run for hours, which is the one thing this exists to prevent.
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        attempts: list[float] = []
        twice = asyncio.Event()

        def _refuse(idle_seconds: float) -> None:
            attempts.append(idle_seconds)
            if len(attempts) >= 2:
                twice.set()
            msg = "the notification channel is gone"
            raise RuntimeError(msg)

        async with _watch(ledger, clock, _refuse).watching():
            await asyncio.wait_for(twice.wait(), timeout=5.0)

        assert len(attempts) >= 2

    async def test_a_raising_notifier_does_not_become_the_cells_outcome(self) -> None:
        # The watch is torn down in a bare finally, so a poller failure
        # re-raised there would replace a measurement that had already
        # succeeded with an unavailable row.
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        reported = asyncio.Event()

        def _refuse(_idle_seconds: float) -> None:
            reported.set()
            msg = "the notification channel is gone"
            raise RuntimeError(msg)

        measured = False
        async with _watch(ledger, clock, _refuse).watching():
            await asyncio.wait_for(reported.wait(), timeout=5.0)
            measured = True

        assert measured

    async def test_a_stall_never_ends_the_cell(self) -> None:
        # The whole point: this observes. A watch that cancelled its cell would
        # turn a slow run into a failed one and score the loop on the cap.
        clock = FakeClock()
        ledger = ProgressTrackingLedger(clock=clock)
        reported = asyncio.Event()
        finished = False

        async with _watch(ledger, clock, lambda _idle: reported.set()).watching():
            await asyncio.wait_for(reported.wait(), timeout=5.0)
            finished = True

        assert finished
