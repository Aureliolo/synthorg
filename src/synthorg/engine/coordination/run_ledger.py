# module-kind: code
"""Which plans have a live dispatch in this process.

A plan's waves are driven by a background task, and until recovery existed
there was exactly one thing that could start one: an operator approving the
plan. Recovery adds a second, which is the whole point of it, so "is anybody
already driving this plan" stops being obvious and becomes a question needing
an owner. Two drivers on one plan would assign the same subtasks twice, and
the engine refuses the second assignment, so the wave that lost the race fails
the plan it was trying to help.

This is that owner, and it is deliberately in-process, because that is exactly
what it claims: the dispatch is a task on this event loop, so a set of plan ids
IS the fact rather than a cache of one. It says nothing about another process,
and must not be read as if it did. Two topologies sit outside it and each has
its own answer:

- The shipped single-backend topology runs every dispatch here, so at boot the
  ledger is empty because nothing can be running: the process that held the
  previous entries is gone.
- A deployment running distributed workers hands execution to the work queue,
  whose redelivery on an unacknowledged claim is the owner of the same question
  there. Recovery defers to it rather than adding a second answer.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.coordination import COORDINATION_RUN_CLAIM_REFUSED

logger = get_logger(__name__)


class LiveRunLedger:
    """The plan ids whose waves are being driven in this process.

    Claiming and releasing are synchronous on purpose: there is no await
    between the test and the write, so a claim cannot be lost to another
    coroutine interleaving between them. An async API would reintroduce
    exactly the race the ledger exists to remove.
    """

    __slots__ = ("_driving",)

    def __init__(self) -> None:
        self._driving: set[str] = set()

    def try_claim(self, plan_id: str) -> bool:
        """Claim *plan_id* for this driver, if nothing else holds it.

        Args:
            plan_id: The plan about to be driven.

        Returns:
            Whether the caller may drive it. ``False`` means somebody else
            is already driving this plan and the caller must not.
        """
        if plan_id in self._driving:
            logger.info(
                COORDINATION_RUN_CLAIM_REFUSED,
                plan_id=plan_id,
                reason="already-driving",
            )
            return False
        self._driving.add(plan_id)
        return True

    def release(self, plan_id: str) -> None:
        """Release *plan_id*, whatever became of the drive.

        Idempotent, so a caller releasing in a ``finally`` after a claim it
        never won (or after an earlier release) is harmless.

        Args:
            plan_id: The plan whose drive has finished.
        """
        self._driving.discard(plan_id)

    def is_driving(self, plan_id: str) -> bool:
        """Whether *plan_id* is being driven in this process.

        Args:
            plan_id: The plan to test.

        Returns:
            Whether a driver holds it.
        """
        return plan_id in self._driving

    def __len__(self) -> int:
        """Return how many plans are being driven.

        Returns:
            The number of live drives.
        """
        return len(self._driving)


__all__ = ["LiveRunLedger"]
