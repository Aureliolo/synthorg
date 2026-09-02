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
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker import CostTracker
from synthorg.client.models import ClientRequest
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.schema import RootConfig
from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    SkillSet,
    ToolPermissions,
)
from synthorg.core.completion_enums import FinishReason
from synthorg.core.project import Project
from synthorg.core.role import Authority, Skill
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RefinementHandoff,
    RoutingVerdict,
    WorkItem,
    WorkSource,
)
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.security.audit import AuditLog
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.workers.runtime_builder import build_runtime_services
from tests._shared import (
    FakeClock,
    make_app_state,
    mock_of,
    sid,
    wire_decomposition_model,
)
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_DECOMPOSITION_TOOL = "submit_decomposition_plan"
_RESEARCH_SKILL = "research"
_ANALYSIS_SKILL = "analysis"

# The work item's definition of done, held here because the scripted plan has
# to claim these criteria VERBATIM: the parser matches a subtask's `satisfies`
# against the objective's own text, so two copies of the sentence would refuse
# the plan the moment one of them was reworded.
_RESEARCH_CRITERION = "Research findings are documented and cited"
_ANALYSIS_CRITERION = "Analysis synthesises the research into a report"

# Path-shaped, so each subtask's declaration is one the workspace can be asked
# about and the scripted turn can satisfy by writing it. A run that declares a
# deliverable and leaves its workspace untouched is a no-op the engine fails on
# purpose, and this harness tests the SPINE rather than delivery.
_RESEARCH_ARTIFACT = "research/sources.md"
_ANALYSIS_ARTIFACT = "analysis/summary.md"


def _declared_artifact(messages: list[ChatMessage]) -> str:
    """Which subtask's declared path this turn is being asked to produce.

    The scripted turn has no task object, only the brief it was handed, so
    the subtask is read back off the title the decomposition wrote into it.

    Returns:
        The declared path for whichever subtask this session is running.
    """
    brief = "\n".join(str(message.content or "") for message in messages)
    if "Analyse the findings" in brief:
        return _ANALYSIS_ARTIFACT
    return _RESEARCH_ARTIFACT


class _DecompositionAwareStrategy:
    """Branches decomposition tool calls vs plain sub-agent turns.

    A sub-agent turn writes its declared artifact before answering, because a
    run that declares deliverables and leaves its workspace as it found it is
    a silent no-op the engine fails on purpose. Scripting the write is what
    makes the dispatched work a delivery rather than a claim of one.
    """

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        del config
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
                                    "stakes": "normal",
                                    "required_role": "developer",
                                    "required_skills": [_RESEARCH_SKILL],
                                    "acceptance_criteria": [
                                        "Data sources are catalogued.",
                                    ],
                                    "satisfies": [_RESEARCH_CRITERION],
                                    "expected_artifacts": [_RESEARCH_ARTIFACT],
                                },
                                {
                                    "id": "sub-analysis",
                                    "title": "Analyse the findings",
                                    "description": "Synthesise results.",
                                    "stakes": "normal",
                                    "required_role": "developer",
                                    "required_skills": [_ANALYSIS_SKILL],
                                    "acceptance_criteria": [
                                        "Findings are summarised.",
                                    ],
                                    "satisfies": [_ANALYSIS_CRITERION],
                                    "expected_artifacts": [_ANALYSIS_ARTIFACT],
                                },
                            ],
                        },
                    ),
                ),
                finish_reason=FinishReason.TOOL_USE,
                usage=usage,
                model=model,
            )
        if not any(m.role is MessageRole.TOOL for m in messages):
            return CompletionResponse(
                content=None,
                tool_calls=(
                    ToolCall(
                        id="work-1",
                        name="write_file",
                        arguments={
                            "path": _declared_artifact(messages),
                            "content": "# delivered by the scripted turn\n",
                            "create_directories": True,
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

    async def process(self, request: ClientRequest) -> IntakeResult:
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
                acceptance_criteria=tuple(
                    AcceptanceCriterion(description=c)
                    for c in request.requirement.acceptance_criteria
                ),
            ),
            requested_by=str(meta["requested_by"]),
        )
        return IntakeResult.accepted_result(
            request_id=request.request_id,
            task_id=str(created.id),
        )


def _make_agent(name: str, skill: str) -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role="developer",
        department="engineering",
        skills=SkillSet(primary=(Skill(id=skill, name=skill),)),
        authority=Authority(budget_limit=10.0),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
        # ``echo`` is ToolCategory.OTHER, which the default STANDARD level
        # excludes; the scripted turns call it, so it is allowed by name.
        # ``write_file`` rides beside it so a dispatched subtask can satisfy
        # the declaration it was given rather than being exempted from it.
        tools=ToolPermissions(allowed=(NotBlankStr("echo"), NotBlankStr("write_file"))),
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


async def _build_pipeline(
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
    await wire_decomposition_model(settings_service)
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
        audit_log=AuditLog(),
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
    agent = _make_agent("solo-dev", _RESEARCH_SKILL)
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
        project=sid("proj-solo"),
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
    alice = _make_agent("alice", _RESEARCH_SKILL)
    bob = _make_agent("bob", _ANALYSIS_SKILL)
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
        project=sid("proj-team"),
        requested_by="operator",
        # A definition of done so the coordinator's clarification gate (on
        # by default) passes and the team path runs; this test exercises
        # the team spine, not the under-specified-work refinement handoff.
        acceptance_criteria=(_RESEARCH_CRITERION, _ANALYSIS_CRITERION),
    )
    result = await pipeline.run(work_item)

    assert result.verdict is RoutingVerdict.SPLITTABLE
    assert result.execution_path is ExecutionPath.TEAM
    assert result.is_success is True

    # Exactly the call the GET /coordination/metrics controller makes.
    records, total = metrics_store.query(limit=10)
    assert total >= 1
    assert records[0].task_id == result.task_id


class _RecordingRefinementRouter:
    """Captures the refinement call and returns a fixed handoff.

    Structurally satisfies the engine's ``WorkRefinementRouter`` port so
    the spine routes under-specified team work here instead of the
    coordinator.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[WorkItem, Task, tuple[str, ...]]] = []

    async def request_refinement(
        self,
        *,
        work_item: WorkItem,
        task: Task,
        reasons: tuple[str, ...],
    ) -> RefinementHandoff:
        self.calls.append((work_item, task, reasons))
        return RefinementHandoff(
            conversation_id="conv-refine-1",
            needs_clarification=True,
            detail="What does done look like for this objective?",
        )


async def test_team_work_without_criteria_routes_to_refinement(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """Team-bound work with no definition of done is refined, not run.

    With a refinement router attached, splittable work that carries no
    acceptance criteria is handed to refinement (the clarification gate
    would otherwise block it): the run reports the ``REFINEMENT`` path
    and a handoff, the coordinator never runs, and no coordination
    metrics are recorded.
    """
    await persistence.projects.create(_project("proj-refine"))
    metrics_store = CoordinationMetricsStore()
    alice = _make_agent("alice", _RESEARCH_SKILL)
    bob = _make_agent("bob", _ANALYSIS_SKILL)
    pipeline = await _build_pipeline(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        routing_policy="always-team",
        agents=(alice, bob),
        metrics_store=metrics_store,
    )
    router = _RecordingRefinementRouter()
    pipeline.attach_refinement_router(router)

    work_item = WorkItem(
        origin_adapter_id="harness",
        source=WorkSource.SIMULATION,
        title="Build something ambitious",
        raw_intent="Decompose into research and analysis work.",
        project=sid("proj-refine"),
        requested_by="operator",
    )
    result = await pipeline.run(work_item)

    assert result.verdict is RoutingVerdict.SPLITTABLE
    assert result.execution_path is ExecutionPath.REFINEMENT
    assert result.is_success is True
    assert result.refinement_handoff is not None
    assert result.refinement_handoff.conversation_id == "conv-refine-1"
    assert result.refinement_handoff.needs_clarification is True
    # The router saw the originating work item, and no coordination ran.
    assert len(router.calls) == 1
    assert router.calls[0][0].title == "Build something ambitious"
    _, total = metrics_store.query(limit=10)
    assert total == 0


def _project(project_id: str) -> Project:
    from synthorg.core.project_enums import ProjectStatus
    from tests._shared import as_uuid

    return Project(
        id=as_uuid(project_id),
        name=project_id,
        description="acceptance project",
        status=ProjectStatus.ACTIVE,
    )
