"""A park somebody is holding is not a death the org replans over.

``BLOCKED`` is reached from several directions and the status alone cannot
tell them apart, so every rule that needs "will anything move this again"
was carrying its own list of reasons. The lists disagreed. A live run:

    completion_oracle.review.escalation_routed  blocked_reason=oracle_escalated
                                                verdict=escalate
    initiative.replan.scheduled                 stall_reason=mixed_dead
    initiative.replan.started                   generation=0

The completion review asked a human to decide, and 500 milliseconds later
the org replanned the initiative, superseding the plan that decision was
about. Nobody was told the question had been withdrawn; the approval the
operator was looking at simply stopped being about anything.

The invariant, which is the one the ``NO_CAPABLE_AGENT`` carve-out already
states and which nothing generalised: a task parked on a decision somebody
else holds can still move, so it is not dead, so it cannot be what makes a
plan stalled. A replan there does not answer the question, it discards it.

Asserted against the declaration rather than the run: every ``BlockedReason``
says who ends its park, and the stall classifier reads that one answer.
"""

import pytest

from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.task_enums import (
    PARK_EXIT,
    STAFFING_BLOCKED_REASONS,
    BlockedReason,
    ParkExit,
    TaskStatus,
)
from synthorg.engine.initiative.completion import (
    ItemProgress,
    StallReason,
    stall_reason,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.unit


def _exits(held_by: ParkExit, *, matching: bool) -> list[BlockedReason]:
    """The reasons whose exit is (or is not) held by *held_by*.

    Args:
        held_by: The exit holder to select on.
        matching: Whether to take the reasons that match it or the rest.

    Returns:
        The reasons, ordered so a parametrised run is reproducible.
    """
    return sorted(
        (
            reason
            for reason, exit_ in PARK_EXIT.items()
            if (exit_ is held_by) is matching
        ),
        key=lambda reason: reason.value,
    )


def _parked(reason: BlockedReason) -> ItemProgress:
    """One outstanding WORK item parked on *reason*.

    Returns:
        The item, as the rollup reads it.
    """
    return ItemProgress(
        item_id=as_uuid(f"item-{reason.value}"),
        kind=PlanItemKind.WORK,
        task_id=as_uuid(f"task-{reason.value}"),
        task_status=TaskStatus.BLOCKED,
        blocked_reason=reason,
    )


class TestEveryParkNamesItsExit:
    def test_the_declaration_covers_every_reason(self) -> None:
        # An undeclared reason would fall through to whatever the reading
        # rule defaults to, which is how one list came to disagree with
        # another. Also asserted at import, so this is the readable half.
        assert set(PARK_EXIT) == set(BlockedReason)

    def test_the_staffing_set_is_derived_from_it(self) -> None:
        assert (
            frozenset(
                reason for reason, exit_ in PARK_EXIT.items() if exit_ is ParkExit.SWEEP
            )
            == STAFFING_BLOCKED_REASONS
        )


class TestAParkSomebodyHoldsIsNotAStall:
    @pytest.mark.parametrize("reason", _exits(ParkExit.REPLAN, matching=False))
    def test_an_attended_park_leaves_the_plan_unstalled(
        self, reason: BlockedReason
    ) -> None:
        assert stall_reason((_parked(reason),)) is None, (
            f"{reason.value} is ended by {PARK_EXIT[reason].value}, so a "
            "replan would discard what they were asked rather than answer it"
        )

    def test_an_escalated_review_is_not_dead_beside_a_failure(self) -> None:
        # The live shape: one item failed and one is waiting on a person.
        # The failure is real and wants redoing, but redoing it means a new
        # plan, and the plan is what the person is being asked about.
        items = (
            _parked(BlockedReason.ORACLE_ESCALATED),
            ItemProgress(
                item_id=as_uuid("item-failed"),
                kind=PlanItemKind.WORK,
                task_id=as_uuid("task-failed"),
                task_status=TaskStatus.FAILED,
            ),
        )

        assert stall_reason(items) is None

    @pytest.mark.parametrize("reason", _exits(ParkExit.REPLAN, matching=True))
    def test_a_park_only_a_replan_can_end_still_stalls(
        self, reason: BlockedReason
    ) -> None:
        # The complement, so the carve-out cannot quietly become "no park
        # ever stalls": a subtask whose inputs died is waiting on nobody.
        assert stall_reason((_parked(reason),)) is StallReason.BLOCKED
