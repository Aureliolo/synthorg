# module-kind: code
"""Where a decomposition reports how far it has got.

A recursive decomposition persists its tree once, at the end, so the plan it is
writing reads ``PLANNING`` with zero items for the whole run. Everything needed
to answer "is this working" lives in the session ledger and lived only in
memory, which left an operator watching a count stay at zero for 54 minutes
with the backend log as the only way to tell working from hung.

A seam rather than a direct write, because the service holds no plan
repository and should not: it decomposes tasks, and which durable row carries
the answer is the wiring layer's business. It is also the layer that knows the
decomposition it started belongs to the shell it opened, which the service
never learns.

Reporting is best-effort by contract: an implementation that cannot record
progress must not fail the decomposition it is describing, because losing the
progress line is cheap and losing an hour of planning is not.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.decomposition_progress import DecompositionProgress


@runtime_checkable
class DecompositionProgressReporter(Protocol):
    """Receives a snapshot each time a decomposition finishes a node."""

    async def report(
        self, *, objective_task_id: str, progress: DecompositionProgress
    ) -> None:
        """Record how far the decomposition of one objective has got.

        Called after each node, so implementations overwrite rather than
        append: the question is "where is this now", and the run's history is
        the event stream's job.

        The objective is named rather than the node, because the snapshot
        describes the whole tree and the row that carries it is the objective's
        plan. Every level below the root is handed its own child task, so a
        report naming the node would name a subtask at depth three.

        Args:
            objective_task_id: The task the whole tree is being planned for.
            progress: The snapshot, taken from the tree's session ledger.
        """
        ...
