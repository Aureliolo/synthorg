# module-kind: code
"""Detached, bounded, deduplicated scheduling for the initiative tail stages.

The rollup fires all three tail stages (replan, integrate, evaluate)
synchronously from a best-effort observer, so ``schedule()`` must return at
once and must not raise: a stage that threw would stall task processing for
every initiative in the process, not just its own. Each stage also has to run
at most once at a time per plan, and under a wall-clock ceiling.

Those are the same three properties in three services, so they live here once.
``ports.py`` states the contract in prose; this is what makes it structural
rather than something each implementation has to remember to re-derive.

Two details are load-bearing and easy to get wrong alone:

**The ceiling is resolved inside the deadline, not before it.** Reading it
goes to the settings database, so resolving it first would leave that read
unbounded while the plan is already marked in-flight, parking the initiative
for the rest of the process. The clock starts on a conservative default and is
relaxed once the configured value is known.

**The in-flight key is released by a done-callback, not a ``finally``.** A
task cancelled before its first step never runs its body, so a ``finally``
inside the coroutine would leak the key permanently.
"""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Mapping

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import BackgroundTaskRegistry

logger = get_logger(__name__)

#: Resolves the configured wall-clock ceiling for one attempt. Async because
#: every stage reads it live from settings, so an operator's change applies to
#: the next attempt without a restart.
DeadlineResolver = Callable[[], Awaitable[float]]


class StageRunner:
    """Spawns one bounded, deduplicated attempt per key, and never raises.

    Args:
        owner: Registry owner label, used as a log field.
        clock: Clock seam seeding the drain deadline.
        skipped_event: Event logged when an attempt is collapsed or refused.
        failed_event: Event logged when an attempt times out or raises.
    """

    __slots__ = ("_closing", "_failed_event", "_inflight", "_skipped_event", "_tasks")

    def __init__(
        self,
        *,
        owner: str,
        clock: Clock,
        skipped_event: str,
        failed_event: str,
    ) -> None:
        self._tasks = BackgroundTaskRegistry(owner=owner, clock=clock)
        self._skipped_event = skipped_event
        self._failed_event = failed_event
        # Set on the first drain. The task engine is still running when the
        # tails are drained, so its observer dispatch can reach a stage after
        # its registry was declared empty; work spawned then would run against
        # a disconnected backend or be destroyed pending. Refusing after the
        # drain is what makes the drain mean something.
        self._closing = False
        # Keys with an attempt in flight this process. The rollup fires on
        # every recompute that reads the stage's status, not on an edge, so
        # without this a burst of task events would each start their own
        # attempt. Checked-and-set synchronously (no await) so it cannot race.
        self._inflight: set[str] = set()

    def start(
        self,
        *,
        key: str,
        work: Coroutine[object, object, None],
        deadline: DeadlineResolver,
        fallback_seconds: float,
        fields: Mapping[str, object],
    ) -> bool:
        """Spawn one detached attempt for *key*, bounded by *deadline*.

        Returns immediately and never raises, so it is safe to call from the
        rollup's best-effort observer.

        Returns:
            ``True`` when an attempt was spawned, ``False`` when the runner is
            shutting down, one was already in flight, or the spawn failed.
        """
        if self._closing:
            logger.debug(self._skipped_event, reason="shutting_down", **fields)
            work.close()
            return False
        if key in self._inflight:
            logger.debug(self._skipped_event, reason="already_inflight", **fields)
            work.close()
            return False
        self._inflight.add(key)
        bounded = self._bounded(work, deadline, fallback_seconds, fields)
        try:
            task = self._tasks.spawn(bounded, event=self._failed_event, **fields)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- schedule() is called from a best-effort
            # observer and is contracted never to raise into it
            reraise_critical(exc)
            self._inflight.discard(key)
            # ``bounded`` never started, so it never reached ``await work``;
            # close both so the inner work coroutine is not left un-awaited.
            bounded.close()
            work.close()
            logger.warning(
                self._failed_event,
                reason="spawn_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                **fields,
            )
            return False
        task.add_done_callback(lambda _task: self._inflight.discard(key))
        return True

    async def drain(self, *, timeout_sec: float) -> None:
        """Refuse new attempts, then wait for the outstanding ones."""
        self._closing = True
        await self.settle(timeout_sec=timeout_sec)

    async def settle(self, *, timeout_sec: float) -> None:
        """Wait for the outstanding attempts without refusing new ones.

        Split from :meth:`drain` so a caller that only needs the in-flight work
        to finish (a test driving one scheduled attempt at a time) does not
        also permanently close the runner.
        """
        await self._tasks.drain(timeout_sec=timeout_sec)

    @property
    def inflight(self) -> frozenset[str]:
        """Keys with an attempt in flight, for assertions and diagnostics."""
        return frozenset(self._inflight)

    async def _bounded(
        self,
        work: Coroutine[object, object, None],
        deadline: DeadlineResolver,
        fallback_seconds: float,
        fields: Mapping[str, object],
    ) -> None:
        """Await *work* under a deadline, swallowing every non-critical failure.

        Raises:
            asyncio.CancelledError: If the attempt is cancelled at shutdown; it
                propagates so the background registry can reap it.
        """
        bound: asyncio.Timeout | None = None
        try:
            async with asyncio.timeout(fallback_seconds) as bound:
                configured = await deadline()
                if configured > 0:
                    bound.reschedule(asyncio.get_running_loop().time() + configured)
                await work
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            # Since 3.11 the stage's own deadline and any inner timeout are the
            # same class, and reporting an inner one as the stage budget sends
            # an operator to retune a setting that was never the problem.
            if bound is not None and bound.expired():
                logger.warning(self._failed_event, reason="timeout", **fields)
            else:
                self._log_failure(exc, fields)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort tail stage; the plan keeps
            # its status and the next rollup event re-fires the attempt
            reraise_critical(exc)
            self._log_failure(exc, fields)

    def _log_failure(self, exc: BaseException, fields: Mapping[str, object]) -> None:
        """Warn that one attempt failed, with the exception redacted."""
        logger.warning(
            self._failed_event,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            **fields,
        )
