"""Acceptance: the work pipeline spine is online behind the switch.

Builds the REAL runtime through the production
``build_runtime_services`` (the exact code the boot hook runs) with a
deterministic ``ScriptedDriver`` and a real ``IntakeEngine`` whose
strategy persists a task via the live ``TaskEngine``. A ``WorkItem``
then enters ``work_pipeline.run`` and flows automatically:

* ``leaf-threshold`` + small sequential work -> LEAF -> single-agent
  execution via the worker execution service; the task reaches a real
  post-execution status (proof an agent actually ran).
* ``always-team`` -> SPLITTABLE -> the multi-agent coordinator runs
  decompose -> route -> dispatch -> rollup and a
  ``CoordinationMetricsRecord`` lands in the store the
  ``GET /coordination/metrics`` controller reads.

Zero real LLM spend: the scripted provider returns a decomposition
plan for the decomposition tool call and a plain STOP completion for
every agent turn.
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker import CostTracker
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.enums import (
    AgentStatus,
    Complexity,
    Priority,
    SeniorityLevel,
    TaskStatus,
    TaskType,
)
from synthorg.core.role import Authority, Skill
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkSource,
)
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.hr.registry import AgentRegistryService
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
from tests._shared import FakeClock, make_app_state, mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_DECOMPOSITION_TOOL = "submit_decomposition_plan"
_RESEARCH_SKILL = "research"
_ANALYSIS_SKILL = "analysis"


class _DecompositionAwareStrategy:
    """Branches decomposition tool calls vs plain sub-agent turns."""

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        del messages, config
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
            content="Work complete.",
            finish_reason=FinishReason.STOP,
            usage=usage,
            model=model,
        )


class _TaskCreatingIntakeStrategy:
    """Deterministic intake: persist a real task via the task engine.

    The work pipeline reads the task back by id, so a stub task id
    (the other harness strategies) is not enough here.
    """

    def __init__(self, task_engine: TaskEngine) -> None:
        self._task_engine = task_engine

    async def process(self, request: Any) -> IntakeResult:
        meta = request.metadata
        created = await self._task_engine.create_task(
            CreateTaskData(
                title=request.requirement.title,
                description=request.requirement.description,
                type=TaskType.DEVELOPMENT,
                project=str(meta["project"]),
                created_by=str(meta["requested_by"]),
                priority=Priority.MEDIUM,
                estimated_complexity=Complexity.MEDIUM,
            ),
            requested_by=str(meta["requested_by"]),
        )
        return IntakeResult.accepted_result(
            request_id=request.request_id,
            task_id=created.id,
        )


def _make_agent(name: str, skill: str, *, level: SeniorityLevel) -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role="developer",
        department="engineering",
        level=level,
        skills=SkillSet(primary=(Skill(id=skill, name=skill),)),
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


async def _build_pipeline(  # noqa: PLR0913 -- test builder with keyword-only knobs
    *,
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
    routing_policy: str,
    agents: tuple[AgentIdentity, ...],
    metrics_store: CoordinationMetricsStore | None = None,
) -> DefaultWorkPipeline:
    provider = ScriptedDriver(
        "test-provider",
        strategy=_DecompositionAwareStrategy(),
    )
    registry = ProviderRegistry({"test-provider": provider})
    agent_registry = AgentRegistryService()
    for agent in agents:
        await agent_registry.register(agent)

    root_config = RootConfig(
        company_name="work-pipeline-spine-test",
        coordination_metrics=CoordinationMetricsConfig(enabled=True),
    )
    settings_service = SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
    )
    await settings_service.set("coordination", "routing_policy", routing_policy)
    config_resolver = ConfigResolver(
        settings_service=settings_service,
        config=root_config,
    )
    intake = IntakeEngine(strategy=_TaskCreatingIntakeStrategy(task_engine))
    app_state = make_app_state(
        provider_registry=registry,
        config=root_config,
        config_resolver=config_resolver,
        task_engine=task_engine,
        agent_registry=agent_registry,
        approval_store=ApprovalStore(),
        clock=FakeClock(),
        agent_workspace_root=tmp_path,
        persistence=persistence,
        client_simulation_state=mock_of[ClientSimulationState](
            intake_engine=intake,
        ),
        cost_tracker=CostTracker(),
        coordination_metrics_store=metrics_store,
    )
    runtime = await build_runtime_services(app_state, workspace_root=tmp_path)
    pipeline = runtime.work_pipeline
    assert isinstance(pipeline, DefaultWorkPipeline)
    return pipeline


async def test_work_item_flows_solo_via_spine(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """Leaf work routes to single-agent execution through the spine."""
    await persistence.projects.create(_project("proj-solo"))
    # MID agent aligns with MEDIUM complexity -> scorer awards the
    # seniority bonus (0.2 >= 0.1 min), so the solo pick is deterministic.
    agent = _make_agent("solo-dev", _RESEARCH_SKILL, level=SeniorityLevel.MID)
    pipeline = await _build_pipeline(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        routing_policy="leaf-threshold",
        agents=(agent,),
    )

    work_item = WorkItem(
        origin_adapter_id="harness",
        source=WorkSource.SIMULATION,
        title="Add a status endpoint",
        raw_intent="First add the route, then return a JSON status body.",
        project="proj-solo",
        requested_by="operator",
    )
    result = await pipeline.run(work_item)

    assert result.verdict is RoutingVerdict.LEAF
    assert result.execution_path is ExecutionPath.SOLO
    assert result.is_success is True
    # A real agent ran: the worker execution service hands the task to
    # the engine which drives it past ASSIGNED. CREATED would mean no
    # agent ran.
    assert result.final_task_status is not TaskStatus.CREATED
    persisted = await task_engine.get_task(result.task_id)
    assert persisted is not None
    assert persisted.assigned_to == str(agent.id)


async def test_work_item_flows_team_and_records_metrics_via_spine(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """Splittable work runs the coordinator and lands a metrics record."""
    await persistence.projects.create(_project("proj-team"))
    metrics_store = CoordinationMetricsStore()
    alice = _make_agent("alice", _RESEARCH_SKILL, level=SeniorityLevel.MID)
    bob = _make_agent("bob", _ANALYSIS_SKILL, level=SeniorityLevel.MID)
    pipeline = await _build_pipeline(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        routing_policy="always-team",
        agents=(alice, bob),
        metrics_store=metrics_store,
    )

    work_item = WorkItem(
        origin_adapter_id="harness",
        source=WorkSource.SIMULATION,
        title="Financial analysis",
        raw_intent="Decompose into research and analysis work.",
        project="proj-team",
        requested_by="operator",
    )
    result = await pipeline.run(work_item)

    assert result.verdict is RoutingVerdict.SPLITTABLE
    assert result.execution_path is ExecutionPath.TEAM
    assert result.is_success is True

    # Exactly the call the GET /coordination/metrics controller makes.
    records, total = metrics_store.query(limit=10)
    assert total >= 1
    assert records[0].task_id == result.task_id


def _project(project_id: str) -> Any:
    from synthorg.core.enums import ProjectStatus
    from synthorg.core.project import Project

    return Project(
        id=project_id,
        name=project_id,
        description="acceptance project",
        status=ProjectStatus.ACTIVE,
    )
