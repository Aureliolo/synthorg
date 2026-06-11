"""Acceptance: a real submitted request executes via the pipeline.

Builds the REAL runtime through the production
``build_runtime_services`` (the exact code the boot hook runs) with a
deterministic ``ScriptedDriver`` and a real ``IntakeEngine`` whose
``DirectIntake`` strategy persists a task via the live ``TaskEngine``.
The real ``IntakeEntryAdapter`` + the
``POST /requests/{id}/approve`` background coroutine
(``process_intake_pipeline``) then drive an approved
``ClientRequest`` through the spine: intake -> projects -> decompose
-> solo execution. The request reaches ``TASK_CREATED`` and the task
reaches a post-execution status (proof an agent actually ran).

Zero real LLM spend: the scripted provider returns a plain STOP
completion for every agent turn.
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers.requests.pipeline import process_intake_pipeline
from synthorg.api.state import AppState
from synthorg.budget.tracker import CostTracker
from synthorg.client.models import ClientRequest, RequestStatus, TaskRequirement
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.completion_enums import FinishReason
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.role import Authority, Skill
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.strategies import DirectIntake
from synthorg.engine.pipeline.entry.boot import _project_uuid
from synthorg.engine.pipeline.entry.intake_adapter import IntakeEntryAdapter
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
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
from tests._shared import FakeClock, make_app_state
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_PROJECT = "client-intake"
# Canonical project id the production boot derives from the slug; the
# entry adapter, intake strategy, and seed all key off this so the
# pipeline's ``projects.get(work_item.project)`` resolves exactly as it
# does in production.
_PROJECT_ID = str(_project_uuid(_PROJECT))
_RESEARCH_SKILL = "research"


class _StopStrategy:
    """Every agent turn is a plain STOP completion (no tool calls)."""

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        del messages, tools, config
        return CompletionResponse(
            content="Work complete.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=8, output_tokens=4, cost=0.0),
            model=model,
        )


def _make_agent() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="solo-dev",
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        skills=SkillSet(primary=(Skill(id=_RESEARCH_SKILL, name=_RESEARCH_SKILL),)),
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


async def _build_app_state(
    *,
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
    sim_state: ClientSimulationState,
) -> AppState:
    """Wire the production runtime + the real intake entry adapter."""
    await persistence.projects.create(
        Project(
            id=_project_uuid(_PROJECT),
            name=_PROJECT,
            description="real intake e2e",
            status=ProjectStatus.ACTIVE,
        )
    )
    provider = ScriptedDriver("test-provider", strategy=_StopStrategy())
    registry = ProviderRegistry({"test-provider": provider})
    agent_registry = AgentRegistryService()
    await agent_registry.register(_make_agent())

    root_config = RootConfig(company_name="real-intake-e2e")
    settings_service = SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
    )
    await settings_service.set("coordination", "routing_policy", "leaf-threshold")
    config_resolver = ConfigResolver(
        settings_service=settings_service,
        config=root_config,
    )
    harness_state = make_app_state(
        provider_registry=registry,
        config=root_config,
        config_resolver=config_resolver,
        task_engine=task_engine,
        agent_registry=agent_registry,
        approval_store=ApprovalStore(),
        clock=FakeClock(),
        agent_workspace_root=tmp_path,
        persistence=persistence,
        client_simulation_state=sim_state,
        cost_tracker=CostTracker(),
    )
    runtime = await build_runtime_services(harness_state, workspace_root=tmp_path)
    assert runtime.work_pipeline is not None
    adapter = IntakeEntryAdapter(
        work_pipeline=runtime.work_pipeline,
        default_project=_PROJECT_ID,
    )
    return make_app_state(
        config=root_config,
        approval_store=ApprovalStore(),
        intake_entry_adapter=adapter,
        client_simulation_state=sim_state,
    )


def _sim_state(task_engine: TaskEngine) -> ClientSimulationState:
    from synthorg.engine.review.pipeline import ReviewPipeline

    return ClientSimulationState(
        intake_engine=IntakeEngine(
            strategy=DirectIntake(task_engine=task_engine, project=_PROJECT_ID),
        ),
        # An empty ReviewPipeline (no stages) keeps ``has_simulation_runtime``
        # truthy without exercising review logic the intake-entry path
        # does not touch; without this, the work-pipeline build short-
        # circuits to ``None`` and every intake-entry assertion fails.
        review_pipeline=ReviewPipeline(stages=()),
        intake_default_project=_PROJECT_ID,
    )


async def test_real_request_executes_through_pipeline(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """An approved request becomes a task an agent actually runs."""
    sim_state = _sim_state(task_engine)
    request = ClientRequest(
        client_id="acme-co",
        requirement=TaskRequirement(
            title="Add a status endpoint",
            description="Return a JSON status body from /status.",
        ),
        status=RequestStatus.APPROVED,
    )
    await sim_state.request_store.save(request)
    app_state = await _build_app_state(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        sim_state=sim_state,
    )

    await process_intake_pipeline(
        app_state=app_state,
        sim_state=sim_state,
        request_id=request.request_id,
    )

    final = await sim_state.request_store.get(request.request_id)
    assert final.status is RequestStatus.TASK_CREATED
    task_id = final.metadata["task_id"]
    assert isinstance(task_id, str)
    persisted = await task_engine.get_task(task_id)
    assert persisted is not None
    # CREATED would mean no agent ran; the worker execution service
    # drives it past ASSIGNED through the scripted provider.
    assert persisted.status is not TaskStatus.CREATED
    assert persisted.project == _PROJECT_ID


async def test_scoped_request_with_notes_executes(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """A manually-scoped request (reviewer notes) still executes."""
    sim_state = _sim_state(task_engine)
    request = ClientRequest(
        client_id="acme-co",
        requirement=TaskRequirement(
            title="Refined title",
            description="Refined description after scoping.",
        ),
        status=RequestStatus.APPROVED,
        metadata={"scoping_notes": "Prioritise the JSON contract."},
    )
    await sim_state.request_store.save(request)
    app_state = await _build_app_state(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        sim_state=sim_state,
    )

    await process_intake_pipeline(
        app_state=app_state,
        sim_state=sim_state,
        request_id=request.request_id,
    )

    final = await sim_state.request_store.get(request.request_id)
    assert final.status is RequestStatus.TASK_CREATED
    task_id = final.metadata["task_id"]
    assert isinstance(task_id, str)
    persisted = await task_engine.get_task(task_id)
    assert persisted is not None
    assert persisted.status is not TaskStatus.CREATED
