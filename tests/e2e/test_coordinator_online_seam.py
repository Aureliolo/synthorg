"""Acceptance: the multi-agent coordinator is online behind the switch.

Builds the REAL coordinator through the production
``build_runtime_services`` (the exact code the boot hook runs) with a
deterministic ``ScriptedDriver``, then drives a decomposable task
through ``coordinate()`` end to end: decompose -> route -> parallel
dispatch -> rollup. The scripted provider is the simulation harness:
its branching strategy returns a valid decomposition plan when called
with the ``submit_decomposition_plan`` tool, and a plain STOP
completion for every sub-agent turn, so the whole pipeline runs without
a live LLM. ``/coordinate`` returns a real ``CoordinationResult`` with
every pipeline phase recorded rather than a 503.

Workspace isolation is wired at boot (``PlannerWorktreeStrategy``) but
the per-run config disables it so the pipeline never touches git: this
test isolates the coordinator runtime, not the git-worktree path
(covered separately).
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.coordination_metric_models import CoordinationMetrics
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.role import Authority, Skill
from synthorg.core.task_enums import Priority, TaskType
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.models import DecompositionContext
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.workers.runtime_builder import build_runtime_services
from tests._shared import FakeClock, make_app_state
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_DECOMPOSITION_TOOL = "submit_decomposition_plan"
_RESEARCH_SKILL = "research"
_ANALYSIS_SKILL = "analysis"


class _DecompositionAwareStrategy:
    """Scripted strategy that branches decomposition vs sub-agent turns.

    The decomposition strategy is the only caller that passes the
    ``submit_decomposition_plan`` tool; it gets a valid two-subtask
    plan back. Every other LLM call is a sub-agent execution turn and
    gets a plain STOP completion so the agent loop terminates in one
    turn without needing a live model.
    """

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        usage = TokenUsage(input_tokens=8, output_tokens=4, cost=0.0001)
        is_decomposition = tools is not None and any(
            t.name == _DECOMPOSITION_TOOL for t in tools
        )
        if is_decomposition:
            return CompletionResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="decomp-1",
                        name=_DECOMPOSITION_TOOL,
                        arguments={
                            # Two independent sub-problems with distinct
                            # required skills so routing assigns them to
                            # different agents and they run in parallel.
                            "task_structure": "parallel",
                            "coordination_topology": "centralized",
                            "subtasks": [
                                {
                                    "id": "sub-research",
                                    "title": "Research the data sources",
                                    "description": "Investigate inputs.",
                                    "required_skills": [_RESEARCH_SKILL],
                                },
                                {
                                    "id": "sub-analysis",
                                    "title": "Analyse the findings",
                                    "description": "Synthesise results.",
                                    "required_skills": [_ANALYSIS_SKILL],
                                },
                            ],
                        },
                    ),
                ),
                finish_reason=FinishReason.TOOL_USE,
                usage=usage,
                model=model,
            )
        return CompletionResponse(
            content="Subtask complete.",
            finish_reason=FinishReason.STOP,
            usage=usage,
            model=model,
        )


def _make_agent(name: str, skill: str) -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        skills=SkillSet(
            primary=(Skill(id=skill, name=skill),),
        ),
        authority=Authority(budget_limit=10.0),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


@pytest.fixture
async def persistence() -> AsyncGenerator[FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.connect()
    yield backend
    await backend.disconnect()


@pytest.fixture
async def task_engine(
    persistence: FakePersistenceBackend,
) -> AsyncGenerator[TaskEngine]:
    engine = TaskEngine(persistence=persistence)
    await engine.start()
    yield engine
    await engine.stop()


async def test_coordinator_runs_decomposable_task_end_to_end(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    provider = ScriptedDriver(
        "test-provider",
        strategy=_DecompositionAwareStrategy(),
    )
    registry = ProviderRegistry({"test-provider": provider})
    agent_registry = AgentRegistryService()
    alice = _make_agent("alice", _RESEARCH_SKILL)
    bob = _make_agent("bob", _ANALYSIS_SKILL)
    await agent_registry.register(alice)
    await agent_registry.register(bob)

    root_config = RootConfig(company_name="coordinator-online-test")
    settings_service = SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
    )
    config_resolver = ConfigResolver(
        settings_service=settings_service,
        config=root_config,
    )
    app_state = make_app_state(
        provider_registry=registry,
        config=root_config,
        config_resolver=config_resolver,
        task_engine=task_engine,
        agent_registry=agent_registry,
        approval_store=ApprovalStore(),
        clock=FakeClock(),
        agent_workspace_root=tmp_path,
    )

    runtime = await build_runtime_services(
        app_state,
        workspace_root=tmp_path,
    )
    coordinator = runtime.coordinator
    assert isinstance(coordinator, MultiAgentCoordinator)

    created = await task_engine.create_task(
        CreateTaskData(
            title="Financial analysis",
            description="Decompose into research and analysis.",
            type=TaskType.DEVELOPMENT,
            project="proj-coord",
            created_by="operator",
            priority=Priority.MEDIUM,
        ),
        requested_by="operator",
    )

    context = CoordinationContext(
        task=created,
        available_agents=(alice, bob),
        decomposition_context=DecompositionContext(max_subtasks=4),
        # Workspace isolation is wired at boot but disabled per-run so
        # the pipeline never touches git in the harness.
        config=CoordinationConfig(
            enable_workspace_isolation=False,
            fail_fast=False,
        ),
    )

    attributed = await coordinator.coordinate(context)
    result = attributed.result

    # /coordinate returns a real result (no 503, no CoordinationPhaseError
    # bubbled out of coordinate()).
    assert result.parent_task_id == str(created.id)
    # Decompose ran: the scripted plan produced two subtasks.
    assert result.decomposition_result is not None
    assert len(result.decomposition_result.plan.subtasks) == 2
    # Route ran: both subtasks routed across the team (alice + bob).
    assert result.routing_result is not None
    assert len(result.routing_result.decisions) == 2
    routed_agents = {
        d.selected_candidate.agent_identity.name
        for d in result.routing_result.decisions
    }
    assert routed_agents == {"alice", "bob"}
    # Parallel dispatch ran and the sub-agents ACTUALLY executed: both
    # subtasks were promoted CREATED -> ASSIGNED for their routed agent,
    # so the engine ran each one and produced an AgentRunResult. A
    # subtask still in CREATED would be rejected at the engine seam with
    # an ExecutionStateError and a ``None`` result, so a non-None result
    # is the proof the dispatch path assigned it.
    assert len(result.waves) >= 1
    executed = [
        outcome
        for wave in result.waves
        if wave.execution_result is not None
        for outcome in wave.execution_result.outcomes
    ]
    assert len(executed) == 2
    assert all(o.result is not None for o in executed)
    # Rollup aggregated the two real per-agent outcomes across the team.
    assert result.status_rollup is not None
    assert result.status_rollup.total == 2
    phase_names = {p.phase for p in result.phases}
    assert {"decompose", "route", "rollup"} <= phase_names
    # The parent began as freshly CREATED; the coordinator walked it
    # through the valid lifecycle so the rollup-derived status was
    # reachable, instead of attempting one invalid hop and recording a
    # failed update_parent phase.
    update_parent = next((p for p in result.phases if p.phase == "update_parent"), None)
    assert update_parent is not None
    assert update_parent.success, update_parent.error
    assert result.total_duration_seconds >= 0.0
    assert isinstance(attributed.agent_contributions, tuple)


async def test_coordinator_records_coordination_metrics_end_to_end(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """A real multi-agent run lands a record the read API would return.

    With ``cost_tracker`` present and ``coordination_metrics.enabled``,
    ``build_runtime_services`` constructs the collector and the
    coordinator's post-completion hook persists a
    ``CoordinationMetricsRecord`` into the ``CoordinationMetricsStore``.
    ``store.query(...)`` is exactly the call the
    ``GET /coordination/metrics`` controller makes, so a non-empty query
    proves the endpoint serves real, persisted coordination metrics
    rather than an empty list.
    """
    provider = ScriptedDriver(
        "test-provider",
        strategy=_DecompositionAwareStrategy(),
    )
    registry = ProviderRegistry({"test-provider": provider})
    agent_registry = AgentRegistryService()
    alice = _make_agent("alice", _RESEARCH_SKILL)
    bob = _make_agent("bob", _ANALYSIS_SKILL)
    await agent_registry.register(alice)
    await agent_registry.register(bob)

    root_config = RootConfig(
        company_name="coordinator-metrics-test",
        coordination_metrics=CoordinationMetricsConfig(enabled=True),
    )
    settings_service = SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
    )
    config_resolver = ConfigResolver(
        settings_service=settings_service,
        config=root_config,
    )
    metrics_store = CoordinationMetricsStore()
    app_state = make_app_state(
        provider_registry=registry,
        config=root_config,
        config_resolver=config_resolver,
        task_engine=task_engine,
        agent_registry=agent_registry,
        approval_store=ApprovalStore(),
        clock=FakeClock(),
        agent_workspace_root=tmp_path,
        cost_tracker=CostTracker(),
        coordination_metrics_store=metrics_store,
    )

    runtime = await build_runtime_services(
        app_state,
        workspace_root=tmp_path,
    )
    coordinator = runtime.coordinator
    assert isinstance(coordinator, MultiAgentCoordinator)

    created = await task_engine.create_task(
        CreateTaskData(
            title="Financial analysis",
            description="Decompose into research and analysis.",
            type=TaskType.DEVELOPMENT,
            project="proj-coord-metrics",
            created_by="operator",
            priority=Priority.MEDIUM,
        ),
        requested_by="operator",
    )

    context = CoordinationContext(
        task=created,
        available_agents=(alice, bob),
        decomposition_context=DecompositionContext(max_subtasks=4),
        config=CoordinationConfig(
            enable_workspace_isolation=False,
            fail_fast=False,
        ),
    )

    attributed = await coordinator.coordinate(context)
    assert attributed.result.parent_task_id == str(created.id)

    # Exactly the call the GET /coordination/metrics controller makes.
    records, total = metrics_store.query(limit=10)
    assert total >= 1
    assert metrics_store.count() >= 1
    record = records[0]
    assert record.task_id == str(created.id)
    # Multi-agent coordination is a system-level run: no single lead.
    assert record.agent_id is None
    assert record.team_size == 2
    assert record.computed_at is not None
    assert isinstance(record.metrics, CoordinationMetrics)
