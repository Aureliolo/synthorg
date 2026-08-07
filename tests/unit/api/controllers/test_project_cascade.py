"""Unit tests for the project-delete cascade's contended plan retirement.

The rollup advances the same plan row whenever a task under it changes, so a
delete issued while the last task completes can lose the race. What matters is
not only that the retry happens but that it re-decides: an itemless shell can
only be FAILED, a filled plan can only be SUPERSEDED, and the race winner is
exactly what turns one into the other.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers._project_cascade import _supersede_plan
from synthorg.api.services.plan_service import PlanService
from synthorg.core.domain_errors import VersionConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.persistence.plan_protocol import PlanRepository
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)

#: Configured ``mock_of`` instance, typed loosely so the ``unittest.mock``
#: assertion API type-checks.
_Configured = Any  # type: ignore[explicit-any]


def _plan(*, status: PlanStatus, filled: bool, version: int = 1) -> Plan:
    items = (
        (
            PlanItem(
                id=NotBlankStr(sid("item-1")),
                title=NotBlankStr("Build it"),
                description=NotBlankStr("Implement the board."),
                acceptance_criteria=(NotBlankStr("it renders"),),
                expected_artifacts=(NotBlankStr("web/src/board.tsx"),),
            ),
        )
        if filled
        else ()
    )
    return Plan(
        id=as_uuid("plan-1"),
        project=NotBlankStr(sid("proj-1")),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the board"),
        parent_task_id=NotBlankStr(sid("task-1")),
        items=items,
        status=status,
        version=version,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestSupersedeUnderContention:
    async def test_the_terminal_is_re_derived_from_the_race_winners_plan(
        self,
    ) -> None:
        """A shell filled by the race winner is superseded, not failed.

        The first attempt sees an itemless PLANNING shell, whose only legal
        terminal is FAILED. It loses to the decomposer, which fills the items
        and parks it for review. Re-deciding is what makes the retry legal:
        FAILED against a filled plan would be wrong, and SUPERSEDED against
        the shell violates the items CHECK.
        """
        shell = _plan(status=PlanStatus.PLANNING, filled=False)
        filled = _plan(status=PlanStatus.PENDING_REVIEW, filled=True, version=2)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=[VersionConflictError("lost"), filled])
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=filled)
        )

        await _supersede_plan(service, repository, shell, requested_by="admin")

        first, second = service.sync_status.await_args_list
        assert first.args[1] is PlanStatus.FAILED
        assert first.kwargs["failure_reason"] == "project deleted"
        assert second.args[0] is filled
        assert second.args[1] is PlanStatus.SUPERSEDED
        # SUPERSEDED forbids a failure_reason, so re-deciding has to drop it.
        assert second.kwargs["failure_reason"] is None

    async def test_a_winner_that_already_retired_it_stops(self) -> None:
        # Nothing is left orphaned, so a second write would only lose another
        # race, and forcing a terminal over a terminal is not the cascade's
        # decision to make.
        live = _plan(status=PlanStatus.PENDING_REVIEW, filled=True)
        retired = _plan(status=PlanStatus.SUPERSEDED, filled=True, version=2)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=VersionConflictError("lost"))
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=retired)
        )

        await _supersede_plan(service, repository, live, requested_by="admin")

        assert service.sync_status.await_count == 1

    async def test_a_plan_the_winner_deleted_stops(self) -> None:
        # The row is gone, so there is nothing to orphan and nothing to write.
        live = _plan(status=PlanStatus.PENDING_REVIEW, filled=True)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=VersionConflictError("lost"))
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=None)
        )

        await _supersede_plan(service, repository, live, requested_by="admin")

        assert service.sync_status.await_count == 1
