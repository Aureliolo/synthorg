"""Acceptance: a conversational work brief drafts a plan for review.

End-to-end through the REAL components, no mocks on the seam under test:

* a real ``ChiefOfStaffProposer`` (scripted provider: turn 1 asks a
  clarifying question, turn 2 emits one concrete work brief),
* a real :class:`ConversationalPlanDispatcher` over the REAL work
  pipeline built by the production ``build_runtime_services``: a work
  brief provisions/reuses the project, runs intake synchronously (a real
  persisted task the chat can subscribe to), and hands the decompose+park
  spine to the background-dispatch port,
* a real ``TaskEngine``: intake creates a persisted task the plan is
  drafted for.

A work brief becomes ONE objective whose owner drafts a single ``Plan``
reviewed holistically in Plan Review (covered by the plan-review resume
suites + the live dogfood), never a per-item approval. This test pins the
propose -> plan-draft seam: the brief reaches the real pipeline as a
plan-gated objective.

Zero real LLM spend: every provider is scripted/deterministic.
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker import CostTracker
from synthorg.client.models import ClientRequest
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.completion_enums import FinishReason
from synthorg.core.project import Project
from synthorg.core.role import Authority, Skill
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.pipeline.models import WorkItem
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import ProposeArgs
from synthorg.meta.chief_of_staff.plan_intake import ConversationalPlanDispatcher
from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.workers.runtime_builder import build_runtime_services
from tests._shared import FakeClock, as_uuid, make_app_state, mock_of, sid
from tests._shared.conversation_fakes import (
    FakeConversationRepo,
    FakeTurnRepo,
)
from tests._shared.scripted_provider import ScriptedProvider, make_text_response
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_RESEARCH_SKILL = "research"

_CLARIFY_JSON = (
    '{"needs_clarification": true, '
    '"clarifying_question": "Which project is the landing page for?", '
    '"work": null}'
)
_WORK_JSON = (
    '{"needs_clarification": false, "clarifying_question": null, '
    '"work": {"title": "Build launch landing page", '
    '"raw_intent": "First scaffold the route, then a JSON status body.", '
    f'"project": "{sid("proj-conv")}", "priority": "medium", '
    '"task_type": "development", "estimated_complexity": "medium", '
    '"acceptance_criteria": ["renders"]}}'
)


class _SoloStrategy:
    """Plain STOP completion for every agent turn (no decomposition)."""

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        del messages, config, tools
        return CompletionResponse(
            content="Work complete.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=8, output_tokens=4, cost=0.0001),
            model=model,
        )


class _TaskCreatingIntakeStrategy:
    """Deterministic intake: persist a real task via the task engine."""

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
            ),
            requested_by=str(meta["requested_by"]),
        )
        return IntakeResult.accepted_result(
            request_id=request.request_id,
            task_id=str(created.id),
        )


class _CapturingDispatchPort:
    """Records the backgrounded spine call without running it.

    The decompose+park spine is exercised by the pipeline + plan-review
    suites; here we pin only that the dispatcher hands it a plan-gated work
    item over the intaken task.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[WorkItem, Task]] = []

    def dispatch_conversational_execution(
        self,
        *,
        work_pipeline: WorkPipeline,
        work_item: WorkItem,
        task: Task,
    ) -> None:
        del work_pipeline
        self.calls.append((work_item, task))


def _make_agent(name: str, skill: str) -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid(name),
        name=name,
        role="developer",
        department="engineering",
        skills=SkillSet(primary=(Skill(id=skill, name=skill),)),
        authority=Authority(budget_limit=10.0),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


def _project(project_id: str) -> Project:
    from synthorg.core.project_enums import ProjectStatus

    return Project(
        id=as_uuid(project_id),
        name=project_id,
        description="acceptance project",
        status=ProjectStatus.ACTIVE,
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
    agents: tuple[AgentIdentity, ...],
) -> DefaultWorkPipeline:
    provider = ScriptedDriver("test-provider", strategy=_SoloStrategy())
    registry = ProviderRegistry({"test-provider": provider})
    agent_registry = AgentRegistryService()
    for agent in agents:
        await agent_registry.register(agent)
    root_config = RootConfig(
        company_name="conversational-propose-e2e",
        coordination_metrics=CoordinationMetricsConfig(enabled=True),
    )
    settings_service = SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
    )
    await settings_service.set("coordination", "routing_policy", "leaf-threshold")
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
        coordination_metrics_store=CoordinationMetricsStore(),
    )
    runtime = await build_runtime_services(app_state, workspace_root=tmp_path)
    pipeline = runtime.work_pipeline
    assert isinstance(pipeline, DefaultWorkPipeline)
    return pipeline


async def test_work_brief_drafts_a_plan_over_the_real_pipeline(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    await persistence.projects.create(_project("proj-conv"))
    agent = _make_agent("solo-dev", _RESEARCH_SKILL)
    pipeline = await _build_pipeline(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        agents=(agent,),
    )
    port = _CapturingDispatchPort()
    dispatcher = ConversationalPlanDispatcher(
        project_repo=persistence.projects,
        work_pipeline=pipeline,
        clock=FakeClock(),
        dispatch_port=port,
    )
    proposer = ChiefOfStaffProposer(
        provider=ScriptedProvider(
            responses=[
                make_text_response(_CLARIFY_JSON),
                make_text_response(_WORK_JSON),
            ]
        ),
        config=ChiefOfStaffConfig(
            propose_enabled=True,
            propose_model=NotBlankStr("test-model-001"),
        ),
        conversation_repo=FakeConversationRepo(),
        turn_repo=FakeTurnRepo(),
        approval_store=ApprovalStore(),
        clock=FakeClock(),
    )
    proposer.attach_plan_dispatcher(dispatcher)

    # Turn 1: vague request -> clarifying question, nothing drafted.
    first = await proposer.converse(
        ProposeArgs(
            message=NotBlankStr("I need a landing page"),
            created_by=NotBlankStr("operator"),
        )
    )
    assert first.status == "needs_clarification"
    assert port.calls == []

    # Turn 2: the answer -> a concrete work brief drafts a plan.
    second = await proposer.converse(
        ProposeArgs(
            message=NotBlankStr("For the proj-conv launch"),
            created_by=NotBlankStr("operator"),
            conversation_id=first.conversation_id,
        )
    )
    assert second.status == "proposed"
    assert second.plan_draft is not None
    assert second.plan_draft.project == sid("proj-conv")

    # Intake ran synchronously against the real pipeline: a real task exists
    # for the drafted plan, and the decompose+park spine was handed to the
    # background port as a plan-gated objective.
    task_id = second.plan_draft.task_id
    persisted = await persistence.tasks.get(NotBlankStr(task_id))
    assert persisted is not None
    assert persisted.project == sid("proj-conv")
    assert len(port.calls) == 1
    dispatched_item, dispatched_task = port.calls[0]
    assert str(dispatched_task.id) == task_id
    assert dispatched_item.plan_required is True
