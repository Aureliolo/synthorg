"""Acceptance: vague request -> clarify -> approve -> pipeline.

End-to-end through the REAL components, no mocks on the seam under
test:

* a real ``ChiefOfStaffProposer`` (scripted provider: turn 1 asks a
  clarifying question, turn 2 emits one concrete proposal),
* a real :class:`ApprovalStore` -- the proposal lands as a PENDING
  ``CONVERSATIONAL_INTAKE`` approval-queue item,
* the production approval-decision seam
  (:func:`signal_resume_intent`): on approval its Flow 0
  (``try_conversational_intake_resume``) rebuilds the ``WorkItem`` and
  drives it through the REAL work pipeline built by the production
  ``build_runtime_services`` over the client-simulation runtime,
* a real ``TaskEngine``: the approved work becomes a persisted task
  that an agent actually advances past CREATED.

Zero real LLM spend: every provider is scripted/deterministic.
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._approval_review_gate import signal_resume_intent
from synthorg.approval.enums import ApprovalStatus
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.coordination_store import CoordinationMetricsStore
from synthorg.budget.tracker import CostTracker
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.communication.conversation.enums import (
    ConversationalProposalStatus,
    ConversationStatus,
)
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
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
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationalProposal,
    ConversationTurn,
    ProposeArgs,
)
from synthorg.meta.chief_of_staff.propose import ChiefOfStaffProposer
from synthorg.persistence.conversation_protocol import (
    ConversationTurnFilterSpec,
)
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalFilterSpec,
)
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.enums import FinishReason
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
from tests._shared.scripted_provider import ScriptedProvider, make_text_response
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_RESEARCH_SKILL = "research"

_CLARIFY_JSON = (
    '{"needs_clarification": true, '
    '"clarifying_question": "Which project is the landing page for?", '
    '"proposals": []}'
)
_PROPOSE_JSON = (
    '{"needs_clarification": false, "clarifying_question": null, '
    '"proposals": [{"title": "Build launch landing page", '
    '"raw_intent": "First scaffold the route, then a JSON status body.", '
    f'"project": "{sid("proj-conv")}", "priority": "medium", '
    '"task_type": "development", "estimated_complexity": "medium", '
    '"acceptance_criteria": ["renders"]}]}'
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


class _FakeProposalRepo:
    def __init__(self) -> None:
        self.items: dict[str, ConversationalProposal] = {}

    async def save(self, entity: ConversationalProposal) -> None:
        self.items[str(entity.id)] = entity

    async def get(self, entity_id: str) -> ConversationalProposal | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ConversationalProposal, ...]:
        return tuple(self.items.values())[offset : offset + limit]

    async def transition_if(
        self,
        entity_id: str,
        from_state: ConversationalProposalStatus,
        to_state: ConversationalProposalStatus,
        **updates: object,
    ) -> bool:
        cur = self.items.get(entity_id)
        if cur is None or cur.status is not from_state:
            return False
        self.items[entity_id] = cur.model_copy(update={"status": to_state})
        return True

    async def query(
        self,
        filter_spec: ConversationalProposalFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ConversationalProposal, ...]:
        rows = [
            p
            for p in self.items.values()
            if filter_spec.approval_id is None
            or p.approval_id == filter_spec.approval_id
        ]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: ConversationalProposalFilterSpec) -> int:
        return len(await self.query(filter_spec))


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


def _project(project_id: str) -> Any:
    from synthorg.core.project import Project
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
    # The legacy ``has_*`` flags are derived from slice contents now; we
    # only wire the actual service references. A ``ClientSimulationState``
    # autospec carries both ``intake_engine`` and ``review_pipeline`` as
    # MagicMock attributes, so ``has_simulation_runtime`` reads truthy
    # without us spelling out a stub review pipeline.
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


async def test_vague_request_clarifies_then_executes_on_approval(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    await persistence.projects.create(_project("proj-conv"))
    agent = _make_agent("solo-dev", _RESEARCH_SKILL, level=SeniorityLevel.MID)
    pipeline = await _build_pipeline(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        agents=(agent,),
    )

    approval_store = ApprovalStore()
    proposal_repo = _FakeProposalRepo()
    proposer = ChiefOfStaffProposer(
        provider=ScriptedProvider(
            responses=[
                make_text_response(_CLARIFY_JSON),
                make_text_response(_PROPOSE_JSON),
            ]
        ),
        config=ChiefOfStaffConfig(propose_enabled=True),
        conversation_repo=_FakeConversationRepo(),
        turn_repo=_FakeTurnRepo(),
        proposal_repo=proposal_repo,
        approval_store=approval_store,
        clock=FakeClock(),
    )

    # Turn 1: vague request -> clarifying question.
    first = await proposer.converse(
        ProposeArgs(
            message=NotBlankStr("I need a landing page"),
            created_by=NotBlankStr("operator"),
        )
    )
    assert first.status == "needs_clarification"
    assert await approval_store.list_items() == ()

    # Turn 2: the answer -> a concrete parked proposal.
    second = await proposer.converse(
        ProposeArgs(
            message=NotBlankStr("For the proj-conv launch"),
            created_by=NotBlankStr("operator"),
            conversation_id=first.conversation_id,
        )
    )
    assert second.status == "proposed"
    assert len(second.proposals) == 1
    approval_id = second.proposals[0].approval_id

    pending = await approval_store.list_items(status=ApprovalStatus.PENDING)
    assert [str(a.id) for a in pending] == [approval_id]
    parked = await approval_store.get(NotBlankStr(approval_id))
    assert parked is not None

    # Human approves: persist the decision (as the controller does),
    # then drive the production approval-decision seam.
    await approval_store.save(
        parked.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": parked.created_at,
                "decided_by": "operator",
            }
        )
    )
    dispatch_state = make_app_state(
        approval_store=approval_store,
        conversational_proposal_repo=proposal_repo,
        work_pipeline=pipeline,
    )
    await signal_resume_intent(
        dispatch_state,
        approval_id,
        approved=True,
        decided_by="operator",
        task_id=None,
    )

    # The proposal executed via the pipeline: a real task exists and an
    # agent advanced it past CREATED.
    proposal = await proposal_repo.get(second.proposals[0].proposal_id)
    assert proposal is not None
    assert proposal.status is ConversationalProposalStatus.EXECUTED

    tasks = await persistence.tasks.list_items()
    assert len(tasks) == 1
    executed = tasks[0]
    assert executed.project == sid("proj-conv")
    assert executed.status is not TaskStatus.CREATED
    assert executed.assigned_to == str(agent.id)


async def test_rejected_proposal_never_touches_pipeline(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    await persistence.projects.create(_project("proj-conv"))
    agent = _make_agent("solo-dev", _RESEARCH_SKILL, level=SeniorityLevel.MID)
    pipeline = await _build_pipeline(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        agents=(agent,),
    )
    approval_store = ApprovalStore()
    proposal_repo = _FakeProposalRepo()
    proposer = ChiefOfStaffProposer(
        provider=ScriptedProvider(responses=[make_text_response(_PROPOSE_JSON)]),
        config=ChiefOfStaffConfig(propose_enabled=True),
        conversation_repo=_FakeConversationRepo(),
        turn_repo=_FakeTurnRepo(),
        proposal_repo=proposal_repo,
        approval_store=approval_store,
        clock=FakeClock(),
    )
    result = await proposer.converse(
        ProposeArgs(
            message=NotBlankStr("Build the proj-conv launch page"),
            created_by=NotBlankStr("operator"),
        )
    )
    approval_id = result.proposals[0].approval_id
    parked = await approval_store.get(NotBlankStr(approval_id))
    assert parked is not None
    await approval_store.save(
        parked.model_copy(
            update={
                "status": ApprovalStatus.REJECTED,
                "decided_at": parked.created_at,
                "decided_by": "operator",
                "decision_reason": NotBlankStr("Not now"),
            }
        )
    )
    dispatch_state = make_app_state(
        approval_store=approval_store,
        conversational_proposal_repo=proposal_repo,
        work_pipeline=pipeline,
    )
    await signal_resume_intent(
        dispatch_state,
        approval_id,
        approved=False,
        decided_by="operator",
        decision_reason="Not now",
        task_id=None,
    )

    proposal = await proposal_repo.get(result.proposals[0].proposal_id)
    assert proposal is not None
    assert proposal.status is ConversationalProposalStatus.REJECTED
    assert await persistence.tasks.list_items() == ()
