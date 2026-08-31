"""Boot-wiring coverage for the budget enforcer.

``_construct_agent_engine`` is the one production call site for
``AgentEngine(...)``. Monthly, daily, project and run-hard-ceiling
enforcement all gate on ``AgentEngine._budget_enforcer`` being set; a
boot path that builds a ``BudgetEnforcer`` without threading it into the
engine leaves every one of those checks structurally unreachable (a
task's own ``budget_limit``/``hard_token_ceiling`` still enforce via the
bare task-only fallback regardless of whether an enforcer is wired).
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


def _app_state(
    *, wire_enforcer: bool, budget_config: BudgetConfig | None = None
) -> tuple[AppState, BudgetEnforcer | None]:
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
    config = budget_config or BudgetConfig()
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
        """Regression guard on ``_construct_agent_engine``'s own wiring:
        ``cost_tracker=`` and ``budget_enforcer=`` are two independent
        reads of the same ``BudgetStateSlice``, so a future edit that
        sources either from somewhere else would desynchronise them.
        """
        app_state, enforcer = _app_state(wire_enforcer=True)
        assert enforcer is not None
        engine = await _engine_for(app_state)
        assert engine._budget_enforcer is not None
        assert engine._cost_tracker is engine._budget_enforcer.cost_tracker
        assert engine._cost_tracker is enforcer.cost_tracker

    async def test_mismatched_cost_tracker_and_enforcer_raises(self) -> None:
        """A future edit that reads ``cost_tracker=`` and ``budget_enforcer=``
        from two different sources must fail loud at construction rather
        than silently picking one: ``AgentEngine.__init__`` enforces this
        identity itself.
        """
        app_state, _ = _app_state(wire_enforcer=False)
        config = BudgetConfig()
        slice_tracker = CostTracker(budget_config=config)
        enforcer_tracker = CostTracker(budget_config=config)
        enforcer = BudgetEnforcer(budget_config=config, cost_tracker=enforcer_tracker)
        app_state.wire(
            BudgetStateSlice,
            budget_config=config,
            cost_tracker=slice_tracker,
            budget_enforcer=enforcer,
        )

        with pytest.raises(ValueError, match="cost_tracker must match"):
            await _engine_for(app_state)

    async def test_unwired_enforcer_leaves_engine_without_one(self) -> None:
        app_state, _ = _app_state(wire_enforcer=False)
        engine = await _engine_for(app_state)
        assert engine._budget_enforcer is None

    async def test_wired_enforcer_actually_enforces_the_run_hard_ceiling(
        self,
    ) -> None:
        """An engine holding a present-but-inert enforcer would pass the
        identity assertions above without ever enforcing anything, so
        this test exercises that the ceiling actually fires rather than
        just that the attribute is set.
        """
        app_state, _ = _app_state(
            wire_enforcer=True, budget_config=BudgetConfig(run_hard_ceiling=0.01)
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
