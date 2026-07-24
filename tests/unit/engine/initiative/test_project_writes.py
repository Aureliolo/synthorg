"""Tests for the version-guarded project writes behind the initiative graph.

These functions carry the retry, the hop walk, and every give-up branch of the
initiative's project side, so each branch is exercised directly here rather
than incidentally through the rollup.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.project_writes import (
    MAX_WRITE_ATTEMPTS,
    advance_project_status,
    link_project_to_plan,
)
from synthorg.persistence.project_protocol import ProjectRepository
from tests._shared import as_uuid, mock_of, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_PROJECT = "proj-1"
_PLAN = "plan-1"


async def _seed(status: ProjectStatus) -> FakePersistenceBackend:
    backend = FakePersistenceBackend()
    await backend.projects.save(
        Project(
            id=as_uuid(_PROJECT),
            name=NotBlankStr("Initiative"),
            status=status,
        )
    )
    return backend


async def _status(backend: FakePersistenceBackend) -> ProjectStatus:
    project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
    assert project is not None
    return project.status


class TestLink:
    """Pointing a project at the plan it is executing."""

    async def test_links_and_activates_a_planning_project(self) -> None:
        backend = await _seed(ProjectStatus.PLANNING)

        linked = await link_project_to_plan(
            backend.projects,
            project_id=NotBlankStr(sid(_PROJECT)),
            plan_id=as_uuid(_PLAN),
        )

        assert linked is not None
        assert linked.plan_id == as_uuid(_PLAN)
        assert await _status(backend) is ProjectStatus.ACTIVE

    async def test_repoints_an_active_project_without_touching_status(self) -> None:
        """The re-plan path: the initiative is live, only the pointer moves."""
        backend = await _seed(ProjectStatus.ACTIVE)

        linked = await link_project_to_plan(
            backend.projects,
            project_id=NotBlankStr(sid(_PROJECT)),
            plan_id=as_uuid("plan-successor"),
        )

        assert linked is not None
        assert linked.plan_id == as_uuid("plan-successor")
        assert await _status(backend) is ProjectStatus.ACTIVE

    async def test_a_paused_project_is_not_activated_by_dispatch(self) -> None:
        backend = await _seed(ProjectStatus.ON_HOLD)

        await link_project_to_plan(
            backend.projects,
            project_id=NotBlankStr(sid(_PROJECT)),
            plan_id=as_uuid(_PLAN),
        )

        assert await _status(backend) is ProjectStatus.ON_HOLD

    async def test_a_missing_project_reports_failure(self) -> None:
        backend = FakePersistenceBackend()

        linked = await link_project_to_plan(
            backend.projects,
            project_id=NotBlankStr(sid("absent")),
            plan_id=as_uuid(_PLAN),
        )

        assert linked is None

    async def test_a_lost_write_retries_and_succeeds(self) -> None:
        backend = await _seed(ProjectStatus.PLANNING)
        stored = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert stored is not None
        repo = mock_of[ProjectRepository](
            get=AsyncMock(return_value=stored),
            update=AsyncMock(
                side_effect=[PersistenceVersionConflictError("raced"), None]
            ),
        )

        linked = await link_project_to_plan(
            repo,
            project_id=NotBlankStr(sid(_PROJECT)),
            plan_id=as_uuid(_PLAN),
        )

        assert linked is not None
        assert repo.update.await_count == 2

    async def test_sustained_contention_gives_up_without_clobbering(self) -> None:
        backend = await _seed(ProjectStatus.PLANNING)
        stored = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert stored is not None
        repo = mock_of[ProjectRepository](
            get=AsyncMock(return_value=stored),
            update=AsyncMock(side_effect=PersistenceVersionConflictError("raced")),
        )

        linked = await link_project_to_plan(
            repo,
            project_id=NotBlankStr(sid(_PROJECT)),
            plan_id=as_uuid(_PLAN),
        )

        assert linked is None
        assert repo.update.await_count == MAX_WRITE_ATTEMPTS


class TestAdvance:
    """Walking a project to its derived status."""

    async def test_a_single_hop_writes_once(self) -> None:
        backend = await _seed(ProjectStatus.EVALUATING)

        advanced = await advance_project_status(
            backend.projects,
            project_id=NotBlankStr(sid(_PROJECT)),
            target=ProjectStatus.COMPLETED,
        )

        assert advanced.project is not None
        assert advanced.project.status is ProjectStatus.COMPLETED
        assert advanced.project.version == 2
        assert advanced.before is ProjectStatus.EVALUATING

    async def test_a_multi_hop_target_lands_every_intermediate_status(self) -> None:
        """The state machine rejects PLANNING -> COMPLETED as a single hop.

        Writing the endpoint directly would persist exactly that transition, so
        the walk must go through ACTIVE and the tail, recording every hop.
        """
        backend = await _seed(ProjectStatus.PLANNING)

        advanced = await advance_project_status(
            backend.projects,
            project_id=NotBlankStr(sid(_PROJECT)),
            target=ProjectStatus.COMPLETED,
        )

        assert advanced.project is not None
        assert advanced.project.status is ProjectStatus.COMPLETED
        # One version bump per hop: PLANNING, ACTIVE, INTEGRATING, EVALUATING,
        # COMPLETED.
        assert advanced.project.version == 5
        assert advanced.before is ProjectStatus.PLANNING

    async def test_already_at_target_is_a_no_op(self) -> None:
        backend = await _seed(ProjectStatus.COMPLETED)

        advanced = await advance_project_status(
            backend.projects,
            project_id=NotBlankStr(sid(_PROJECT)),
            target=ProjectStatus.COMPLETED,
        )

        assert advanced.project is not None
        assert advanced.project.version == 1

    async def test_the_observed_status_is_reported_for_the_edge_test(self) -> None:
        """A caller firing once on the edge into COMPLETED needs this read.

        Its own read of the project can be overtaken between the two calls, so
        the only status that can be trusted for the edge is the one the
        winning write itself computed from.
        """
        backend = await _seed(ProjectStatus.ACTIVE)

        advanced = await advance_project_status(
            backend.projects,
            project_id=NotBlankStr(sid(_PROJECT)),
            target=ProjectStatus.COMPLETED,
        )

        assert advanced.before is ProjectStatus.ACTIVE
        assert advanced.project is not None
        assert advanced.project.status is ProjectStatus.COMPLETED

    async def test_an_unreachable_target_leaves_the_project_alone(self) -> None:
        """A cancelled project is terminal; the rollup defers to the operator."""
        backend = await _seed(ProjectStatus.CANCELLED)

        advanced = await advance_project_status(
            backend.projects,
            project_id=NotBlankStr(sid(_PROJECT)),
            target=ProjectStatus.COMPLETED,
        )

        assert advanced.project is not None
        assert advanced.project.status is ProjectStatus.CANCELLED
        assert await _status(backend) is ProjectStatus.CANCELLED

    async def test_a_missing_project_reports_failure(self) -> None:
        backend = FakePersistenceBackend()

        advanced = await advance_project_status(
            backend.projects,
            project_id=NotBlankStr(sid("absent")),
            target=ProjectStatus.COMPLETED,
        )

        assert advanced.project is None
        assert advanced.before is None

    async def test_a_hop_that_loses_its_write_restarts_from_a_fresh_read(self) -> None:
        backend = await _seed(ProjectStatus.PLANNING)
        stored = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert stored is not None
        repo = mock_of[ProjectRepository](
            get=AsyncMock(return_value=stored),
            update=AsyncMock(
                side_effect=[
                    PersistenceVersionConflictError("raced"),
                    None,
                    None,
                    None,
                    None,
                ]
            ),
        )

        advanced = await advance_project_status(
            repo,
            project_id=NotBlankStr(sid(_PROJECT)),
            target=ProjectStatus.COMPLETED,
        )

        assert advanced.project is not None
        # The lost first hop is retried, then all four hops land.
        assert repo.update.await_count == 5
        assert repo.get.await_count == 2

    async def test_sustained_contention_gives_up(self) -> None:
        backend = await _seed(ProjectStatus.ACTIVE)
        stored = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert stored is not None
        repo = mock_of[ProjectRepository](
            get=AsyncMock(return_value=stored),
            update=AsyncMock(side_effect=PersistenceVersionConflictError("raced")),
        )

        advanced = await advance_project_status(
            repo,
            project_id=NotBlankStr(sid(_PROJECT)),
            target=ProjectStatus.COMPLETED,
        )

        assert advanced.project is None
        assert repo.update.await_count == MAX_WRITE_ATTEMPTS
