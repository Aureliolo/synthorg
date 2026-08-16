# module-kind: adapter
"""Chief-of-Staff-backed work refinement router.

Implements the engine's ``WorkRefinementRouter`` port by opening a
Chief-of-Staff clarify-and-propose conversation for under-specified
team-bound work. The spine calls this when a task reaches the team path
with no definition of done: rather than mobilising a team against
undefined work (which the coordinator's clarification gate blocks), the
Chief of Staff clarifies with the human and parks concrete,
criteria-bearing proposals behind the approval queue. Nothing executes
until an approved proposal flows back through the work pipeline.
"""

from synthorg.core.task import Task
from synthorg.engine.pipeline.models import RefinementHandoff, WorkItem
from synthorg.meta.chief_of_staff.models import ProposeArgs, ProposeResult
from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
from synthorg.observability import get_logger
from synthorg.observability.events.chief_of_staff import COS_REFINEMENT_OPENED

logger = get_logger(__name__)


class ChiefOfStaffRefinementRouter:
    """Refines under-specified team work via the Chief of Staff.

    Structurally satisfies the engine's ``WorkRefinementRouter`` port;
    wired onto the work pipeline by the startup hook so the engine never
    imports the meta layer.
    """

    __slots__ = ("_proposer",)

    def __init__(self, *, proposer: ChiefOfStaffProposer) -> None:
        """Initialise the router.

        Args:
            proposer: The Chief-of-Staff clarify-and-propose service that
                owns the refinement conversation and the approval queue.
        """
        self._proposer = proposer

    async def request_refinement(
        self,
        *,
        work_item: WorkItem,
        task: Task,
        reasons: tuple[str, ...],
    ) -> RefinementHandoff:
        """Open a Chief-of-Staff refinement conversation for *work_item*.

        The objective's title and description seed one clarify-or-propose
        turn (the proposer wraps them as untrusted human content). The
        turn either asks a clarifying question or parks concrete proposals
        for approval; either way the human continues from the returned
        conversation.

        Args:
            work_item: The originating entry envelope.
            task: The parent task that lacks a definition of done.
            reasons: Why refinement was triggered (logged for context).

        Returns:
            The :class:`RefinementHandoff` carrying the conversation to
            continue and a human-readable summary of the turn outcome.
        """
        result = await self._proposer.converse(
            ProposeArgs(
                message=f"{work_item.title}\n\n{work_item.raw_intent}",
                created_by=work_item.requested_by,
                project=work_item.project,
            )
        )
        handoff = _to_handoff(result)
        logger.info(
            COS_REFINEMENT_OPENED,
            task_id=str(task.id),
            conversation_id=handoff.conversation_id,
            needs_clarification=handoff.needs_clarification,
            reason_count=len(reasons),
        )
        return handoff


def _to_handoff(result: ProposeResult) -> RefinementHandoff:
    """Map a propose-turn result onto the engine's refinement handoff.

    Returns:
        A :class:`RefinementHandoff` describing whether the turn asked a
        clarifying question or acted (parked steering directives).
    """
    if result.status == "needs_clarification":
        question = result.clarifying_question
        assert question is not None  # noqa: S101 -- guaranteed by ProposeResult
        return RefinementHandoff(
            conversation_id=result.conversation_id,
            needs_clarification=True,
            detail=question,
        )
    parts: list[str] = []
    if result.steering:
        parts.append(f"parked {len(result.steering)} steering directive(s)")
    return RefinementHandoff(
        conversation_id=result.conversation_id,
        needs_clarification=False,
        detail="; ".join(parts) if parts else "no action taken",
    )
