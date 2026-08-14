"""Tests for the role-staffing selection ladder.

The gates ask HR "who holds this role and which of them fits this work".
The answer decides WHO reviews; it never rewrites what anybody runs.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity
from synthorg.core.persistence_errors import QueryError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.hr.role_staffing import (
    RoleStaffingService,
    load_project_for_selection,
)
from synthorg.persistence.project_protocol import ProjectRepository
from tests._shared import as_uuid, mock_of
from tests._shared.staffing import role_holder, staffing_with

pytestmark = pytest.mark.unit

_ROLE = NotBlankStr(COMPLETION_REVIEWER_ROLE_NAME)
_EV = "test.project_read_failed"


def _holder(
    label: str,
    *,
    capability: CapabilityLevel | None = "capable",
    role: str = COMPLETION_REVIEWER_ROLE_NAME,
) -> AgentIdentity:
    """Build a role holder whose bound model carries *capability*.

    A thin default-binder over the shared builder, so this module and every
    other gate test agree on what a role holder is.
    """
    return role_holder(label, role=role, capability=capability, per_label_model=True)


def _project(*, team: tuple[str, ...] = (), lead: str | None = None) -> Project:
    return Project(
        id=as_uuid("proj-apollo"),
        name="Apollo",
        team=team,
        lead=lead,
        status=ProjectStatus.ACTIVE,
    )


def _service(
    *holders: AgentIdentity, executor: AgentIdentity | None = None
) -> RoleStaffingService:
    return staffing_with(*holders, executor=executor)


class TestExecutorCapabilityFloor:
    """A judge is never weaker than the agent whose work it judges.

    The work's stakes and complexity set the requirement, and for a subtask
    both are proposed by the agent that decomposed it. Without a floor that
    lets the thing under review bid its own judge down, because the fit
    ladder PREFERS an exact match: a lower bar does not merely permit a
    weaker reviewer, it selects one.
    """

    async def test_a_stronger_executor_raises_the_requirement(self) -> None:
        executor = _holder("author", capability="expert", role="Developer")
        weak = _holder("weak", capability="basic")
        strong = _holder("strong", capability="expert")

        result = await _service(weak, strong, executor=executor).select_holder(
            role=_ROLE,
            required_capability="basic",
            exclude_agent_id=NotBlankStr(str(executor.id)),
            project=None,
        )

        assert result is not None
        assert result.agent.id == strong.id
        assert result.required_capability == "expert"

    async def test_a_weaker_executor_leaves_the_requirement_alone(self) -> None:
        executor = _holder("author", capability="basic", role="Developer")
        exact = _holder("exact", capability="capable")

        result = await _service(exact, executor=executor).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr(str(executor.id)),
            project=None,
        )

        assert result is not None
        assert result.required_capability == "capable"
        assert result.capability_fit == "match"

    async def test_an_unclassified_executor_leaves_the_requirement_alone(self) -> None:
        executor = _holder("author", capability=None, role="Developer")
        exact = _holder("exact", capability="capable")

        result = await _service(exact, executor=executor).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr(str(executor.id)),
            project=None,
        )

        assert result is not None
        assert result.required_capability == "capable"

    async def test_the_floor_never_invents_a_holder(self) -> None:
        """Raising the bar changes who is preferred, never whether one exists.

        The ladder still falls to a weaker holder when nothing better is on
        the roster: a weaker reviewer is a real independent reviewer, and
        refusing one would trade a real review for none at all.
        """
        executor = _holder("author", capability="expert", role="Developer")
        weak = _holder("weak", capability="basic")

        result = await _service(weak, executor=executor).select_holder(
            role=_ROLE,
            required_capability="basic",
            exclude_agent_id=NotBlankStr(str(executor.id)),
            project=None,
        )

        assert result is not None
        assert result.agent.id == weak.id
        assert result.capability_fit == "lower"


class TestNoCandidates:
    """No holder at all is the unstaffed case the gates fail closed on."""

    async def test_no_holders_yields_none(self) -> None:
        result = await _service().select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )
        assert result is None

    async def test_the_only_holder_being_the_executor_yields_none(self) -> None:
        # The executor may hold the reviewer role; it still may not review its
        # own work, and no independent reviewer remains.
        executor = _holder("solo")
        result = await _service(executor).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr(str(executor.id)),
            project=None,
        )
        assert result is None


class TestProjectPreference:
    """An on-team holder is preferred; widening is explicit."""

    async def test_prefers_a_holder_on_the_project_team(self) -> None:
        on_team = _holder("on-team")
        elsewhere = _holder("elsewhere")
        project = _project(team=(str(on_team.id), "someone-else"))

        result = await _service(elsewhere, on_team).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=project,
        )

        assert result is not None
        assert result.agent.id == on_team.id
        assert result.source == "project_team"

    async def test_the_project_lead_counts_as_on_team(self) -> None:
        lead = _holder("lead")
        elsewhere = _holder("elsewhere")
        project = _project(team=("someone-else",), lead=str(lead.id))

        result = await _service(elsewhere, lead).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=project,
        )

        assert result is not None
        assert result.agent.id == lead.id

    async def test_widens_org_wide_when_the_team_holds_nobody_eligible(self) -> None:
        elsewhere = _holder("elsewhere")
        project = _project(team=("someone-else",))

        result = await _service(elsewhere).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=project,
        )

        assert result is not None
        assert result.agent.id == elsewhere.id
        assert result.source == "org_wide"

    async def test_no_project_reads_org_wide(self) -> None:
        anyone = _holder("anyone")
        result = await _service(anyone).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )
        assert result is not None
        assert result.source == "org_wide"


class TestCapabilityFit:
    """Match first, then higher, then lower; never a model swap."""

    async def test_prefers_an_exact_capability_match(self) -> None:
        weak = _holder("weak", capability="basic")
        exact = _holder("exact", capability="capable")
        strong = _holder("strong", capability="expert")

        result = await _service(weak, strong, exact).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )

        assert result is not None
        assert result.agent.id == exact.id
        assert result.capability_fit == "match"

    async def test_goes_higher_when_no_exact_match_exists(self) -> None:
        weak = _holder("weak", capability="basic")
        strong = _holder("strong", capability="expert")

        result = await _service(weak, strong).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )

        assert result is not None
        assert result.agent.id == strong.id
        assert result.capability_fit == "higher"

    async def test_takes_the_nearest_higher_rung(self) -> None:
        nearer = _holder("nearer", capability="capable")
        further = _holder("further", capability="expert")

        result = await _service(further, nearer).select_holder(
            role=_ROLE,
            required_capability="basic",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )

        assert result is not None
        assert result.agent.id == nearer.id

    async def test_falls_back_to_lower_only_when_nothing_higher_exists(self) -> None:
        # A weaker reviewer is still a real independent reviewer; refusing one
        # would trade a real review for no review at all.
        weak = _holder("weak", capability="basic")

        result = await _service(weak).select_holder(
            role=_ROLE,
            required_capability="expert",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )

        assert result is not None
        assert result.agent.id == weak.id
        assert result.capability_fit == "lower"

    async def test_takes_the_nearest_lower_rung(self) -> None:
        nearer = _holder("nearer", capability="capable")
        further = _holder("further", capability="basic")

        result = await _service(further, nearer).select_holder(
            role=_ROLE,
            required_capability="expert",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )

        assert result is not None
        assert result.agent.id == nearer.id

    async def test_an_unclassified_model_reads_as_the_weakest_rung(self) -> None:
        # An unclassified pair must never outrank a classified one by default;
        # otherwise a model nobody graded would win every selection.
        unclassified = _holder("unclassified", capability=None)
        classified = _holder("classified", capability="capable")

        result = await _service(unclassified, classified).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )

        assert result is not None
        assert result.agent.id == classified.id


class TestDeterminism:
    """Two equally-fit holders resolve the same way every time."""

    async def test_ties_break_on_the_lowest_agent_id(self) -> None:
        """The tie-break is a specific holder, not merely a stable one.

        Asserting only that two orderings agree passes under any symmetric
        rule, including one that picks the last candidate seen; the rule is
        that ties sort on ``str(agent.id)``, so the test names the winner.
        """
        first = _holder("aaa", capability="capable")
        second = _holder("bbb", capability="capable")
        expected = min(first, second, key=lambda agent: str(agent.id))

        forward = await _service(first, second).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )
        reversed_order = await _service(second, first).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )

        assert forward is not None
        assert reversed_order is not None
        assert forward.agent.id == expected.id
        assert reversed_order.agent.id == expected.id

    async def test_the_selection_reports_what_it_was_asked_for(self) -> None:
        holder = _holder("holder", capability="expert")
        result = await _service(holder).select_holder(
            role=_ROLE,
            required_capability="capable",
            exclude_agent_id=NotBlankStr("executor"),
            project=None,
        )
        assert result is not None
        assert result.required_capability == "capable"
        assert result.role == COMPLETION_REVIEWER_ROLE_NAME
        assert result.reason


class TestLoadingTheProject:
    """The project read is a preference, so its failure is not the gate's."""

    async def test_no_repo_or_no_project_reads_nothing(self) -> None:
        repo = mock_of[ProjectRepository]()

        assert (
            await load_project_for_selection(None, NotBlankStr("p1"), failure_event=_EV)
            is None
        )
        assert await load_project_for_selection(repo, None, failure_event=_EV) is None
        repo.get.assert_not_awaited()

    async def test_the_project_is_returned_when_it_reads(self) -> None:
        project = _project(team=("someone",))
        repo = mock_of[ProjectRepository](get=AsyncMock(return_value=project))

        loaded = await load_project_for_selection(
            repo, NotBlankStr(str(project.id)), failure_event=_EV
        )

        assert loaded is project

    async def test_a_failed_read_degrades_to_org_wide_rather_than_blocking(
        self,
    ) -> None:
        """A momentarily unavailable store must not stop the gate.

        Losing the read costs only the on-team preference: a gate role
        reaches every project anyway, so widening is the honest degradation
        and refusing to review would be a worse one.
        """
        repo = mock_of[ProjectRepository](
            get=AsyncMock(side_effect=QueryError("project store is down"))
        )

        with structlog.testing.capture_logs() as logs:
            loaded = await load_project_for_selection(
                repo, NotBlankStr("p1"), failure_event=_EV
            )

        assert loaded is None
        assert any(entry["event"] == _EV for entry in logs)

    async def test_cancellation_is_not_swallowed_as_a_failed_read(self) -> None:
        """A stopping worker is not a store outage, and must not read as one."""
        repo = mock_of[ProjectRepository](
            get=AsyncMock(side_effect=asyncio.CancelledError)
        )

        with pytest.raises(asyncio.CancelledError):
            await load_project_for_selection(repo, NotBlankStr("p1"), failure_event=_EV)
