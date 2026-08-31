"""Boot-wiring coverage for the budget enforcer.

``_construct_agent_engine`` is the one production call site for
``AgentEngine(...)``. Task, monthly, daily, project and run-hard-ceiling
enforcement all gate on ``AgentEngine._budget_enforcer`` being set; a
boot path that builds a ``BudgetEnforcer`` without threading it into the
engine leaves every one of those checks structurally unreachable.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.state import SettingsStateSlice
from synthorg.tools.registry import ToolRegistry
from synthorg.workers._engine_assembly import _construct_agent_engine
from tests._shared import FakeClock, as_uuid, make_app_state, mock_of
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.unit


def _app_state(*, wire_enforcer: bool) -> tuple[AppState, BudgetEnforcer | None]:
    """A minimal boot ``AppState``, optionally with a real budget enforcer wired.

    Returns:
        ``(app_state, enforcer)``: the composed state and the exact
        enforcer instance wired onto its ``BudgetStateSlice``, or
        ``(app_state, None)`` when *wire_enforcer* is ``False``.
    """
    resolver = mock_of[ConfigResolver](
        get_float=AsyncMock(return_value=0.5),
        get_int=AsyncMock(return_value=1),
        get_str=AsyncMock(return_value=""),
        get_bool=AsyncMock(return_value=False),
        get_provider_configs=AsyncMock(return_value={}),
    )
    persistence = mock_of[PersistenceBackend](
        is_connected=True,
        projects=mock_of[ProjectRepository](),
    )
    app_state = make_app_state(
        config=RootConfig(company_name="test"),
        clock=FakeClock(),
        persistence=persistence,
        approval_store=ApprovalStore(),
        agent_registry=AgentRegistryService(),
        task_engine=mock_of[TaskEngine](),
        slices={SettingsStateSlice: {"config_resolver": resolver}},
    )
    if not wire_enforcer:
        return app_state, None
    config = BudgetConfig()
    tracker = CostTracker(budget_config=config)
    enforcer = BudgetEnforcer(budget_config=config, cost_tracker=tracker)
    app_state.wire(
        BudgetStateSlice,
        budget_config=config,
        cost_tracker=tracker,
        budget_enforcer=enforcer,
    )
    return app_state, enforcer


async def _engine_for(app_state: AppState) -> AgentEngine:
    return await _construct_agent_engine(
        app_state,
        ScriptedProvider([]),
        registry=ProviderRegistry(drivers={}),
        tool_registry=ToolRegistry([]),
        coordination_metrics_collector=None,
    )


class TestBudgetEnforcerBootWiring:
    async def test_engine_receives_the_wired_enforcer(self) -> None:
        app_state, enforcer = _app_state(wire_enforcer=True)
        engine = await _engine_for(app_state)
        assert engine._budget_enforcer is enforcer

    async def test_cost_tracker_identity_invariant_holds(self) -> None:
        app_state, enforcer = _app_state(wire_enforcer=True)
        assert enforcer is not None
        engine = await _engine_for(app_state)
        assert engine._budget_enforcer is not None
        assert engine._cost_tracker is engine._budget_enforcer.cost_tracker
        assert engine._cost_tracker is enforcer.cost_tracker

    async def test_unwired_enforcer_leaves_engine_without_one(self) -> None:
        app_state, _ = _app_state(wire_enforcer=False)
        engine = await _engine_for(app_state)
        assert engine._budget_enforcer is None

    async def test_wired_enforcer_actually_enforces_the_run_hard_ceiling(
        self,
    ) -> None:
        """An engine holding a present-but-inert enforcer would pass the
        identity assertions above without ever enforcing anything: the
        claim this PR makes is that the ceiling fires, not just that the
        attribute is set.
        """
        app_state, _ = _app_state(wire_enforcer=False)
        config = BudgetConfig(run_hard_ceiling=0.01)
        tracker = CostTracker(budget_config=config)
        enforcer = BudgetEnforcer(budget_config=config, cost_tracker=tracker)
        app_state.wire(
            BudgetStateSlice,
            budget_config=config,
            cost_tracker=tracker,
            budget_enforcer=enforcer,
        )
        engine = await _engine_for(app_state)
        task = Task(
            id=as_uuid("checker-task"),
            title="Ship it",
            description="Deliver the slice.",
            type=TaskType.DEVELOPMENT,
            project="proj-checker",
            created_by="ceo",
            assigned_to="agent-1",
            status=TaskStatus.ASSIGNED,
        )

        checker = await engine._build_budget_checker(task, "agent-1", project_id=None)

        assert checker is not None
        assert checker.ceilings.cost_ceiling == pytest.approx(0.01)
