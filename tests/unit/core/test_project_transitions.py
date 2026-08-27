"""Tests for the project lifecycle state machine transitions."""

import pytest

from synthorg.core.project_enums import ProjectStatus
from synthorg.core.project_transitions import (
    VALID_TRANSITIONS,
    transition_path,
    validate_transition,
)


@pytest.mark.unit
class TestValidTransitions:
    """Every transition the project lifecycle accepts."""

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            # A project starts unplanned and becomes active once its plan is
            # approved and dispatched.
            (ProjectStatus.PLANNING, ProjectStatus.ACTIVE),
            # The tail mirrors the plan's: assemble, score, then deliver.
            (ProjectStatus.ACTIVE, ProjectStatus.INTEGRATING),
            (ProjectStatus.INTEGRATING, ProjectStatus.EVALUATING),
            (ProjectStatus.EVALUATING, ProjectStatus.COMPLETED),
            # A regressed item reopens the build from either tail stage.
            (ProjectStatus.INTEGRATING, ProjectStatus.ACTIVE),
            (ProjectStatus.EVALUATING, ProjectStatus.ACTIVE),
            # An operator can pause and resume an initiative.
            (ProjectStatus.ACTIVE, ProjectStatus.ON_HOLD),
            (ProjectStatus.INTEGRATING, ProjectStatus.ON_HOLD),
            (ProjectStatus.EVALUATING, ProjectStatus.ON_HOLD),
            (ProjectStatus.ON_HOLD, ProjectStatus.ACTIVE),
            # A terminally-failed plan is derivable from every status that can
            # be executing one.
            (ProjectStatus.PLANNING, ProjectStatus.FAILED),
            (ProjectStatus.ACTIVE, ProjectStatus.FAILED),
            (ProjectStatus.INTEGRATING, ProjectStatus.FAILED),
            (ProjectStatus.EVALUATING, ProjectStatus.FAILED),
            # A fresh plan walks the project back out of failure.
            (ProjectStatus.FAILED, ProjectStatus.PLANNING),
            (ProjectStatus.FAILED, ProjectStatus.ACTIVE),
            # Termination is a human act, available from every live status.
            (ProjectStatus.PLANNING, ProjectStatus.CANCELLED),
            (ProjectStatus.ACTIVE, ProjectStatus.CANCELLED),
            (ProjectStatus.INTEGRATING, ProjectStatus.CANCELLED),
            (ProjectStatus.EVALUATING, ProjectStatus.CANCELLED),
            (ProjectStatus.ON_HOLD, ProjectStatus.CANCELLED),
            (ProjectStatus.FAILED, ProjectStatus.CANCELLED),
        ],
        ids=lambda p: p.value if isinstance(p, ProjectStatus) else str(p),
    )
    def test_valid_transition(
        self, source: ProjectStatus, target: ProjectStatus
    ) -> None:
        validate_transition(source, target)


@pytest.mark.unit
class TestInvalidTransitions:
    """Transitions the project lifecycle must refuse."""

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            # Completion requires passing through ACTIVE: an unplanned project
            # has no work to have completed.
            (ProjectStatus.PLANNING, ProjectStatus.COMPLETED),
            # A paused project must be resumed before it can complete, so the
            # rollup cannot silently finish work an operator deliberately held.
            (ProjectStatus.ON_HOLD, ProjectStatus.COMPLETED),
            # The tail is unskippable, mirroring the plan's machine.
            (ProjectStatus.ACTIVE, ProjectStatus.COMPLETED),
            (ProjectStatus.ACTIVE, ProjectStatus.EVALUATING),
            (ProjectStatus.INTEGRATING, ProjectStatus.COMPLETED),
            # Pausing is only meaningful for work in flight.
            (ProjectStatus.PLANNING, ProjectStatus.ON_HOLD),
            # Backwards moves are not part of the lifecycle.
            (ProjectStatus.ACTIVE, ProjectStatus.PLANNING),
            # A paused initiative has no plan running to fail, so deriving one
            # would finish an operator's deliberate hold out from under them.
            (ProjectStatus.ON_HOLD, ProjectStatus.FAILED),
            # A failed initiative is replanned before it can deliver.
            (ProjectStatus.FAILED, ProjectStatus.COMPLETED),
            (ProjectStatus.FAILED, ProjectStatus.ON_HOLD),
        ],
        ids=lambda p: p.value if isinstance(p, ProjectStatus) else str(p),
    )
    def test_invalid_transition(
        self, source: ProjectStatus, target: ProjectStatus
    ) -> None:
        with pytest.raises(ValueError, match="Invalid project status transition"):
            validate_transition(source, target)

    @pytest.mark.parametrize(
        "terminal",
        [ProjectStatus.COMPLETED, ProjectStatus.CANCELLED],
        ids=lambda p: p.value,
    )
    def test_terminal_has_no_outgoing_transitions(
        self, terminal: ProjectStatus
    ) -> None:
        assert VALID_TRANSITIONS[terminal] == frozenset()


@pytest.mark.unit
class TestLifecycleCoverage:
    """The table must stay in step with the enum."""

    def test_every_status_has_an_entry(self) -> None:
        assert set(VALID_TRANSITIONS) == set(ProjectStatus)

    def test_completed_is_reachable_only_from_evaluating(self) -> None:
        """The forcing property, mirrored from the plan's machine."""
        sources = {
            source
            for source, targets in VALID_TRANSITIONS.items()
            if ProjectStatus.COMPLETED in targets
        }
        assert sources == {ProjectStatus.EVALUATING}

    def test_failed_is_reachable_only_from_a_status_that_can_hold_a_plan(self) -> None:
        """The boundary: a terminally-failed plan derives it, nothing else.

        Task state cannot, because it flaps (an oracle REJECT reworks a task
        and a FAILED task stays reassignable). ON_HOLD cannot, because a paused
        initiative has no plan running to fail.
        """
        sources = {
            source
            for source, targets in VALID_TRANSITIONS.items()
            if ProjectStatus.FAILED in targets
        }
        assert sources == {
            ProjectStatus.PLANNING,
            ProjectStatus.ACTIVE,
            ProjectStatus.INTEGRATING,
            ProjectStatus.EVALUATING,
        }

    def test_failed_is_not_terminal(self) -> None:
        """A fresh plan walks the project back out.

        That is what keeps FAILED a statement about the current plan rather
        than a verdict on the initiative.
        """
        assert VALID_TRANSITIONS[ProjectStatus.FAILED] == frozenset(
            {
                ProjectStatus.PLANNING,
                ProjectStatus.ACTIVE,
                ProjectStatus.CANCELLED,
            }
        )


@pytest.mark.unit
class TestTransitionPath:
    """Multi-hop pathing used to advance a project to a rolled-up status."""

    def test_path_from_planning_to_completed_routes_through_the_tail(self) -> None:
        assert transition_path(ProjectStatus.PLANNING, ProjectStatus.COMPLETED) == (
            ProjectStatus.ACTIVE,
            ProjectStatus.INTEGRATING,
            ProjectStatus.EVALUATING,
            ProjectStatus.COMPLETED,
        )

    def test_path_to_same_status_is_empty(self) -> None:
        assert transition_path(ProjectStatus.ACTIVE, ProjectStatus.ACTIVE) == ()

    def test_path_from_terminal_is_none(self) -> None:
        assert transition_path(ProjectStatus.CANCELLED, ProjectStatus.ACTIVE) is None
