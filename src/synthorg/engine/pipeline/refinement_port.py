# module-kind: declarative
"""Port the work pipeline uses to refine under-specified team work.

Dependency inversion: the pipeline depends on this port, not on the
meta-layer Chief-of-Staff proposer that implements it, so the engine
never imports the meta layer. When team-bound work reaches the spine
with no definition of done, the spine hands it to the router instead of
mobilising a team against undefined work; the router opens a
human-in-the-loop refinement conversation and returns a
:class:`RefinementHandoff` the caller can surface. Nothing executes
until the refined, criteria-bearing work is approved.
"""

from typing import Protocol, runtime_checkable

from synthorg.core.task import Task
from synthorg.engine.pipeline.models import RefinementHandoff, WorkItem


@runtime_checkable
class WorkRefinementRouter(Protocol):
    """Opens a refinement conversation for under-specified team work."""

    async def request_refinement(
        self,
        *,
        work_item: WorkItem,
        task: Task,
        reasons: tuple[str, ...],
    ) -> RefinementHandoff:
        """Hand *work_item* to human-in-the-loop refinement.

        Args:
            work_item: The originating entry envelope.
            task: The persisted parent task that lacks a definition of
                done (the clarification gate would block its decomposition).
            reasons: Why the work needs refinement (the clarification
                gate's reasons), surfaced to the human.

        Returns:
            A :class:`RefinementHandoff` the caller surfaces so the human
            can continue the refinement conversation.
        """
        ...
