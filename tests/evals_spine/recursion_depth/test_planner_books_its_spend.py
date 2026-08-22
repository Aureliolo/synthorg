# module-kind: tests
"""The shipped planner reports what it spent, including when it produces nothing.

Driven against the real ``AgentSessionPlanner`` rather than a scripted double,
because the double books whatever the test tells it to: what needs covering is
this class's own read of a real ledger on the path that raises, which is where
a cell's spend was previously lost outright.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from evals.errors import RecursionDepthPlannerSubstitutedError
from evals.harness.binding import RunBinding
from evals.harness.stall_watch import ProgressTrackingLedger
from evals.recursion_depth import planner as planner_module
from evals.recursion_depth.grading import UnitGrader
from evals.recursion_depth.manifest import ModelPair
from evals.recursion_depth.planner import AgentSessionPlanner, PlanningSpend
from evals.recursion_depth.session import SessionLimits, SweepDeps
from evals.recursion_depth.staffing import SweepRoster
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import CurrencyCode
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.errors import DecompositionError
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.role_staffing import RoleStaffingService
from synthorg.providers.protocol import CompletionProvider
from synthorg.tools.registry import ToolRegistry
from synthorg.tools.sandbox import SandboxBackend
from tests._shared import as_uuid, mock_of, sid
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.unit

_EXECUTOR = ModelPair(
    provider=NotBlankStr("example-provider"),
    model_id=NotBlankStr("example-capable-001"),
    capability="capable",
    family=NotBlankStr("example-family-a"),
)


def _lead() -> AgentIdentity:
    """Build the lead the planning session runs as.

    Returns:
        The identity.
    """
    return AgentIdentity(
        id=as_uuid("identity:lead"),
        name=NotBlankStr("Lead"),
        role=NotBlankStr("Developer"),
        department=NotBlankStr("Engineering"),
        model=ModelConfig(
            provider=_EXECUTOR.provider,
            model_id=_EXECUTOR.model_id,
            capability="capable",
        ),
        hiring_date=datetime(2026, 1, 1, tzinfo=UTC).date(),
    )


def _objective() -> Task:
    """Build the root objective being decomposed.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid("task:objective"),
        title=NotBlankStr("Build the tiny thing"),
        description=NotBlankStr("Build it."),
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project=NotBlankStr(sid("project:planner-spend")),
        created_by=NotBlankStr("test"),
    )


def _record(*, input_tokens: int, output_tokens: int) -> CostRecord:
    """Build one call's cost record.

    Priced at zero on purpose: that is the flat-rate case, where the token
    count is the only figure that moves and the one a cost-only read misses.

    Returns:
        The record.
    """
    return CostRecord(
        provider=_EXECUTOR.provider,
        model=_EXECUTOR.model_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=0.0,
        currency=CurrencyCode("USD"),
        timestamp=datetime(2026, 8, 22, tzinfo=UTC),
    )


@pytest.fixture
def ledger() -> ProgressTrackingLedger:
    """The ledger the planner reads its spend off.

    Returns:
        A tracker the test seeds and the planner drains.
    """
    return ProgressTrackingLedger()


def _planner(ledger: ProgressTrackingLedger) -> AgentSessionPlanner:
    """Build the planner under test over *ledger*.

    Returns:
        The planner, wired to a provider that is never called (the tree build
        is replaced) and to the test's own ledger.
    """

    @asynccontextmanager
    async def _open(_execution_id: str) -> AsyncIterator[ProgressTrackingLedger]:
        yield ledger

    async def _provider(_binding: RunBinding) -> CompletionProvider:
        return ScriptedProvider([])

    deps = SweepDeps(
        build_provider=_provider,
        build_tool_registry=lambda _workspace: mock_of[ToolRegistry](),
        build_grader=lambda _workspace: mock_of[UnitGrader](),
        build_sandbox=lambda root: mock_of[SandboxBackend](),
        open_run_ledger=_open,
    )
    return AgentSessionPlanner(
        deps=deps,
        roster=SweepRoster(
            registry=mock_of[AgentRegistryService](),
            staffing=mock_of[RoleStaffingService](),
            builders=(_lead(),),
            reviewers=(),
        ),
        executor=_EXECUTOR,
        limits=SessionLimits(max_turns=4, cost_ceiling=0.0, token_ceiling=0),
    )


class TestFailedPlanningBooksItsSpend:
    """A planning attempt that raises has still paid for the calls it made."""

    async def test_a_refused_substitute_books_every_level_it_planned(
        self,
        ledger: ProgressTrackingLedger,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The one refusal raised with a finished tree in hand.

        `_refuse_substituted_planner` fires only after every level has planned
        and been billed, so booking the usual floor of one would under-report
        the sweep's own ceiling by everything beneath the root.
        """
        await ledger.record(_record(input_tokens=100, output_tokens=200))

        def _refuse(**_kwargs: object) -> DecompositionResult:
            msg = "the plan was produced by a substitute planner"
            raise RecursionDepthPlannerSubstitutedError(msg, sessions=4)

        monkeypatch.setattr(planner_module, "build_tree", _refuse)
        spend = PlanningSpend()

        with pytest.raises(RecursionDepthPlannerSubstitutedError):
            await _planner(ledger).plan(
                task=_objective(),
                depth_cap=2,
                execution_id="cell-plan",
                spend=spend,
            )

        assert spend.sessions == 4
        assert spend.tokens == 300
        assert spend.cost == pytest.approx(0.0)

    async def test_any_other_failure_books_the_root_session(
        self,
        ledger: ProgressTrackingLedger,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A floor of one: `decompose_task` attempts the root's own planning
        # call before any recursion, and what it split into cannot be counted
        # without the tree the failure withheld.
        await ledger.record(_record(input_tokens=40, output_tokens=60))

        def _fail(**_kwargs: object) -> DecompositionResult:
            msg = "provider call failed"
            raise DecompositionError(msg)

        monkeypatch.setattr(planner_module, "build_tree", _fail)
        spend = PlanningSpend()

        with pytest.raises(DecompositionError):
            await _planner(ledger).plan(
                task=_objective(),
                depth_cap=2,
                execution_id="cell-plan",
                spend=spend,
            )

        assert spend.sessions == 1
        assert spend.tokens == 100
