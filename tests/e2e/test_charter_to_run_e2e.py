"""Acceptance: vague idea -> deep interview -> charter -> approved run.

End-to-end through the REAL components, no mocks on the seam under test:

* a real ``CharterInterviewService`` + ``LLMCharterInterviewer`` (scripted
  provider: two elicitation questions, then a complete charter draft),
* a real ``CharterDispatcher``: on approval it creates the project,
  persists an APPROVED forecast, and drives the kickoff ``WorkItem``
  through the REAL work pipeline built by ``build_runtime_services``,
* a real ``TaskEngine``: the approved charter becomes a persisted task an
  agent actually advances past CREATED, carrying the charter's budget
  ceiling and forecast id (the budget-truth-end-to-end claim).

Zero real LLM spend: every provider is scripted/deterministic.
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from uuid import uuid4, uuid5

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.services.project_service import ProjectService
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.tracker import CostTracker
from synthorg.client.models import ClientRequest
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.communication.conversation.enums import ConversationStatus
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.completion_enums import FinishReason
from synthorg.core.role import Authority, Skill
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.charter.dispatch import PROJECT_NAMESPACE, CharterDispatcher
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import InterviewTurnArgs, ProjectCharter
from synthorg.meta.charter.service import CharterInterviewService
from synthorg.meta.charter.strategy import LLMCharterInterviewer
from synthorg.meta.chief_of_staff.models import Conversation, ConversationTurn
from synthorg.persistence.charter_protocol import CharterFilterSpec
from synthorg.persistence.conversation_protocol import ConversationTurnFilterSpec
from synthorg.persistence.cost_forecast_protocol import CostForecastFilterSpec
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
from tests._shared import FakeClock, make_app_state, mock_of
from tests._shared.scripted_provider import ScriptedProvider, make_text_response
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_RESEARCH_SKILL = "research"
_AMOUNT = 5000.0
_CURRENCY = "USD"

_Q1 = '{"needs_more": true, "next_question": "What is the budget?", "draft": null}'
_Q2 = (
    '{"needs_more": true, '
    '"next_question": "What is in and out of scope?", "draft": null}'
)
_DRAFT = (
    '{"needs_more": false, "next_question": null, "draft": {'
    '"title": "Better memory layer", '
    '"brief": "Build a self-hostable alternative to the incumbent memory tool.", '
    '"goals": ["beat baseline recall"], "constraints": ["self-hostable"], '
    '"success_criteria": ["recall beats baseline by 10%"], '
    '"scope": {"in_scope": ["retrieval"], "out_of_scope": ["billing"]}, '
    '"envelope": {"amount": 5000, "currency": "USD", '
    '"deadline": null, "time_horizon": "1 month"}, '
    '"project_id": null, "proposed_project_name": "memory-layer", '
    '"proposed_project_description": "A better memory layer."}}'
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


class _FakeConversationRepo:
    def __init__(self) -> None:
        self.items: dict[str, Conversation] = {}

    async def save(self, entity: Conversation) -> None:
        self.items[str(entity.id)] = entity

    async def get(self, entity_id: str) -> Conversation | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[Conversation, ...]:
        return tuple(self.items.values())[offset : offset + limit]

    async def transition_if(
        self,
        entity_id: str,
        from_state: ConversationStatus,
        to_state: ConversationStatus,
        **updates: object,
    ) -> bool:
        cur = self.items.get(entity_id)
        if cur is None or cur.status is not from_state:
            return False
        self.items[entity_id] = cur.model_copy(update={"status": to_state})
        return True


class _FakeTurnRepo:
    def __init__(self) -> None:
        self.turns: list[ConversationTurn] = []

    async def append(self, event: ConversationTurn) -> None:
        self.turns.append(event)

    async def query(
        self,
        filter_spec: ConversationTurnFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ConversationTurn, ...]:
        rows = [
            t
            for t in self.turns
            if filter_spec.conversation_id is None
            or t.conversation_id == filter_spec.conversation_id
        ]
        rows.sort(key=lambda t: t.sequence, reverse=True)
        return tuple(rows[offset : offset + limit])

    async def purge_before(self, threshold: object) -> int:
        del threshold
        return 0


class _FakeCharterRepo:
    def __init__(self) -> None:
        self.items: dict[str, ProjectCharter] = {}

    async def save(self, entity: ProjectCharter) -> None:
        self.items[entity.id] = entity

    async def get(self, entity_id: str) -> ProjectCharter | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ProjectCharter, ...]:
        return tuple(self.items.values())[offset : offset + limit]

    async def count(self, filter_spec: CharterFilterSpec) -> int:
        return len(await self.query(filter_spec, limit=len(self.items)))

    async def query(
        self,
        filter_spec: CharterFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ProjectCharter, ...]:
        rows = [
            c
            for c in self.items.values()
            if (filter_spec.status is None or c.status is filter_spec.status)
            and (
                filter_spec.conversation_id is None
                or c.conversation_id == filter_spec.conversation_id
            )
        ]
        return tuple(rows[offset : offset + limit])

    async def transition_if(
        self,
        entity_id: str,
        from_state: CharterStatus,
        to_state: CharterStatus,
        **updates: object,
    ) -> bool:
        cur = self.items.get(entity_id)
        if cur is None or cur.status is not from_state:
            return False
        patch: dict[str, object] = {"status": to_state}
        for key in (
            "approved_at",
            "approved_by",
            "forecast_id",
            "correlation_id",
            "task_id",
        ):
            if key in updates:
                patch[key] = updates[key]
        self.items[entity_id] = cur.model_copy(update=patch)
        return True


class _FakeForecastRepo:
    def __init__(self) -> None:
        self.items: dict[str, Forecast] = {}

    async def save(self, entity: Forecast) -> None:
        self.items[str(entity.forecast_id)] = entity

    async def get(self, entity_id: object) -> Forecast | None:
        return self.items.get(str(entity_id))

    async def delete(self, entity_id: object) -> bool:
        return self.items.pop(str(entity_id), None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[Forecast, ...]:
        rows = tuple(self.items.values())
        return rows[offset : offset + limit]

    async def transition_if(
        self,
        entity_id: object,
        from_state: ForecastDecision,
        to_state: ForecastDecision,
        **updates: object,
    ) -> bool:
        cur = self.items.get(str(entity_id))
        if cur is None or cur.decision is not from_state:
            return False
        self.items[str(entity_id)] = cur.model_copy(update={"decision": to_state})
        return True

    async def query(
        self,
        filter_spec: CostForecastFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        rows = [
            f
            for f in self.items.values()
            if (
                filter_spec.brief_hash is None or f.brief_hash == filter_spec.brief_hash
            )
            and (filter_spec.decision is None or f.decision is filter_spec.decision)
        ]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: CostForecastFilterSpec) -> int:
        return len(await self.query(filter_spec, limit=len(self.items) or 1))


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
        company_name="charter-to-run-e2e",
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


async def test_vague_idea_becomes_approved_charter_that_runs(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    agent = _make_agent("solo-dev", _RESEARCH_SKILL, level=SeniorityLevel.MID)
    pipeline = await _build_pipeline(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        agents=(agent,),
    )
    conversation_repo = _FakeConversationRepo()
    charter_repo = _FakeCharterRepo()
    forecast_repo = _FakeForecastRepo()
    service = CharterInterviewService(
        strategy=LLMCharterInterviewer(
            provider=ScriptedProvider(
                responses=[
                    make_text_response(_Q1),
                    make_text_response(_Q2),
                    make_text_response(_DRAFT),
                ]
            ),
            config=CharterConfig(interview_enabled=True),
        ),
        config=CharterConfig(interview_enabled=True),
        conversation_repo=conversation_repo,
        turn_repo=_FakeTurnRepo(),
        charter_repo=charter_repo,
        clock=FakeClock(),
    )

    # Turn 1: a vague one-line idea -> first elicitation question.
    first = await service.run_turn(
        InterviewTurnArgs(
            message=NotBlankStr("build a better alternative to the memory tool"),
            created_by=NotBlankStr("operator"),
        )
    )
    assert first.status == "needs_more"
    conv_id = NotBlankStr(first.conversation_id)

    # Turn 2: budget answer -> second question.
    second = await service.run_turn(
        InterviewTurnArgs(
            message=NotBlankStr("budget is 5000 USD over a month"),
            created_by=NotBlankStr("operator"),
            conversation_id=conv_id,
        )
    )
    assert second.status == "needs_more"

    # Turn 3: scope answer -> the interview converges on a charter draft.
    third = await service.run_turn(
        InterviewTurnArgs(
            message=NotBlankStr("retrieval is in scope, billing is out"),
            created_by=NotBlankStr("operator"),
            conversation_id=conv_id,
        )
    )
    assert third.status == "drafted"
    assert third.charter is not None
    charter_id = third.charter.id
    assert third.charter.status is CharterStatus.DRAFTED

    # Approve: create the project + an approved forecast, and drive the run.
    dispatcher = CharterDispatcher(
        charter_repo=charter_repo,
        forecast_repo=forecast_repo,
        project_service=ProjectService(repo=persistence.projects),
        work_pipeline=pipeline,
        conversation_repo=conversation_repo,
        budget_currency=lambda: _CURRENCY,
        clock=FakeClock(),
    )
    result = await dispatcher.approve(charter_id, approved_by=NotBlankStr("operator"))

    # The charter is APPROVED with full dispatch provenance.
    assert result.charter.status is CharterStatus.APPROVED
    assert result.charter.approved_by == "operator"
    assert result.charter.forecast_id is not None
    assert result.charter.task_id == result.task_id

    # A new project was created at the deterministic charter-derived id.
    expected_project = str(uuid5(PROJECT_NAMESPACE, f"charter-{charter_id}"))
    assert result.project_id == expected_project
    project = await persistence.projects.get(NotBlankStr(expected_project))
    assert project is not None
    assert project.budget == pytest.approx(_AMOUNT)

    # An APPROVED forecast is the budget record, with the envelope ceiling.
    # Assert the cardinality so a duplicate forecast write does not slip
    # through silently (the dispatcher must upsert by ``forecast_id``).
    assert len(forecast_repo.items) == 1
    forecast = next(iter(forecast_repo.items.values()))
    assert forecast.decision is ForecastDecision.APPROVED
    assert forecast.ceiling_amount == pytest.approx(_AMOUNT)

    # The spine created a real task carrying the budget ceiling + forecast id
    # (the charter actually drove the run end-to-end).
    tasks = await persistence.tasks.list_items()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.project == expected_project
    assert str(task.id) == result.task_id
    assert task.hard_ceiling == pytest.approx(_AMOUNT)
    assert task.forecast_id == forecast.forecast_id
    assert task.status is not TaskStatus.CREATED
    assert task.assigned_to == str(agent.id)

    # The interview conversation was closed on approval.
    conversation = conversation_repo.items[conv_id]
    assert conversation.status is ConversationStatus.CLOSED
