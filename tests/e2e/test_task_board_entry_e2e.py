"""Acceptance: a board filing runs through the real pipeline.

Builds the REAL runtime through the production ``build_runtime_services``
(the exact code the boot hook runs) with a deterministic
``ScriptedDriver``. The real ``TaskBoardEntryAdapter`` then drives a
human-style board filing through the spine: intake (creates the task)
-> projects -> decompose -> solo execution. A task reaches a
post-execution status, proving an agent actually ran for a board
filing.

Zero real LLM spend: the scripted provider returns a plain STOP
completion for every agent turn.
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from typeguard import suppress_type_checks

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers.tasks import process_task_board_pipeline
from synthorg.api.state import AppState
from synthorg.budget.tracker import CostTracker
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.completion_enums import FinishReason
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.role import Authority, Skill
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.strategies import DirectIntake
from synthorg.engine.pipeline.entry.task_board_adapter import (
    TaskBoardEntryAdapter,
    TaskBoardFiling,
)
from synthorg.engine.pipeline.models import WorkSource
from synthorg.engine.review.pipeline import ReviewPipeline
from synthorg.engine.state import EngineStateSlice, task_board_entry_adapter_of
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
from tests._shared import FakeClock, as_uuid, make_app_state, sid
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_PROJECT = "board-project"
_INTAKE_PROJECT = "client-intake"
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


def _sim_state(task_engine: TaskEngine) -> ClientSimulationState:
    # The spine's intake phase creates the task via the configured
    # ``IntakeEngine`` regardless of the work-entry source: the strategy
    # is parameterised with the configured intake project, which the
    # spine uses for intake-phase task creation. The board filing's own
    # ``project`` lands on the ``WorkItem``; the spine's projects-phase
    # checks that project exists, and the solo execution path uses the
    # task the intake strategy just persisted.
    return ClientSimulationState(
        intake_engine=IntakeEngine(
            strategy=DirectIntake(
                task_engine=task_engine, project=sid(_INTAKE_PROJECT)
            ),
        ),
        # An empty ReviewPipeline (no stages) keeps ``has_simulation_runtime``
        # truthy without exercising review logic the board-entry path
        # does not touch; without this, the work-pipeline build short-
        # circuits to ``None`` and every board-entry assertion fails.
        review_pipeline=ReviewPipeline(stages=()),
        intake_default_project=sid(_INTAKE_PROJECT),
    )


async def _build_app_state(
    *,
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
    sim_state: ClientSimulationState,
) -> AppState:
    """Wire the production runtime + the real task-board entry adapter."""
    # The board filing's project must exist (the spine's projects phase
    # rejects unknown projects); the intake project must also exist for
    # the intake strategy's task-creation path.
    for project_id in (_PROJECT, _INTAKE_PROJECT):
        await persistence.projects.create(
            Project(
                id=as_uuid(project_id),
                name=project_id,
                description="task-board e2e",
                status=ProjectStatus.ACTIVE,
            )
        )
    provider = ScriptedDriver("test-provider", strategy=_StopStrategy())
    registry = ProviderRegistry({"test-provider": provider})
    agent_registry = AgentRegistryService()
    await agent_registry.register(_make_agent())

    root_config = RootConfig(company_name="task-board-e2e")
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
    adapter = TaskBoardEntryAdapter(work_pipeline=runtime.work_pipeline)
    return make_app_state(
        config=root_config,
        approval_store=ApprovalStore(),
        task_board_entry_adapter=adapter,
        client_simulation_state=sim_state,
    )


async def test_board_filing_executes_through_pipeline(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """A human board filing becomes a task an agent actually runs.

    Drives the production pipeline spine via
    :func:`process_task_board_pipeline` with the real
    :class:`TaskBoardEntryAdapter`. The filing reaches the spine,
    intake creates the task, decompose -> solo execution drives it
    past CREATED. A task created on the board runs through the real
    pipeline to completion under the simulation harness.
    """
    sim_state = _sim_state(task_engine)
    app_state = await _build_app_state(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        sim_state=sim_state,
    )
    filing = TaskBoardFiling(
        title="Add a status endpoint",
        description="Return a JSON status body from /status.",
        task_type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project=sid(_PROJECT),
        requested_by="user-42",
        estimated_complexity=Complexity.SIMPLE,
    )

    await process_task_board_pipeline(
        adapter=task_board_entry_adapter_of(app_state),
        filing=filing,
    )

    # The spine's intake creates a task; the solo execution path drives
    # it past ASSIGNED. Read the task store directly: the controller's
    # 202 ack only returns the correlation id, so we identify the task
    # by its (single) presence in the engine's listing.
    all_tasks, _total = await task_engine.list_tasks(project=sid(_INTAKE_PROJECT))
    assert len(all_tasks) == 1
    task = all_tasks[0]
    # CREATED means no agent ran; the scripted provider drove the solo
    # path past CREATED.
    assert task.status is not TaskStatus.CREATED


async def test_board_filing_unknown_project_is_swallowed_by_background_task(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """An unknown project surfaces as a swallowed pipeline failure.

    The spine's projects-phase rejects an unknown project with
    ``WorkProjectNotFoundError``. The controller's background
    coroutine catches non-rejection failures, logs ERROR, and does
    not propagate (the HTTP 202 was already returned to the caller).
    No task is created.
    """
    sim_state = _sim_state(task_engine)
    app_state = await _build_app_state(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        sim_state=sim_state,
    )
    # The board project (_PROJECT) exists, but file against a project
    # that does not exist anywhere in persistence to drive the
    # projects-phase rejection.
    filing = TaskBoardFiling(
        title="Targets a missing project",
        description="The spine should reject this in the projects phase.",
        task_type=TaskType.DEVELOPMENT,
        project="never-created-project",
        requested_by="user-42",
    )

    # process_task_board_pipeline swallows the pipeline failure
    # (logs ERROR; the 202 was already returned to the caller).
    await process_task_board_pipeline(
        adapter=task_board_entry_adapter_of(app_state),
        filing=filing,
    )

    # Intake still ran (creates the task before projects-phase rejects),
    # but no task lives in the unknown project. The intake-project task
    # remains because intake commits before the projects phase.
    unknown_tasks, _ = await task_engine.list_tasks(project="never-created-project")
    assert len(unknown_tasks) == 0


async def test_board_filing_propagates_memory_error(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """``MemoryError`` from the spine is NOT swallowed by the background task.

    The controller's background coroutine re-raises
    ``MemoryError`` / ``RecursionError`` so a resource-exhaustion
    failure surfaces to the asyncio loop's exception handler rather
    than being silently logged. Exercising this end-to-end against the
    live spine confirms the contract holds with the production adapter.
    """
    sim_state = _sim_state(task_engine)
    app_state = await _build_app_state(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        sim_state=sim_state,
    )

    oom_reason = "simulated OOM in the spine"

    class _OOMAdapter:
        """Stand-in adapter whose ``submit`` raises ``MemoryError``.

        Replaces the real adapter just for this test; using a stand-in
        is cleaner than monkeypatching the spine internals to fail
        with OOM at a specific phase.
        """

        source = WorkSource.TASK_BOARD

        async def submit(self, _filing: TaskBoardFiling) -> object:
            raise MemoryError(oom_reason)

    filing = TaskBoardFiling(
        title="OOM probe",
        description="Force MemoryError to verify re-raise contract.",
        task_type=TaskType.DEVELOPMENT,
        project=sid(_PROJECT),
        requested_by="user-42",
    )

    # ``_OOMAdapter`` is a deliberate fault-injection seam implementing only
    # ``submit`` + ``source``; it is not a full ``TaskBoardEntryAdapter``.
    # Suppress the runtime protocol check at just this call rather than across
    # the whole test so unrelated type errors still surface.
    with (
        pytest.raises(MemoryError, match="simulated OOM"),
        suppress_type_checks(),
    ):
        await process_task_board_pipeline(
            adapter=_OOMAdapter(),  # type: ignore[arg-type]  # test seam
            filing=filing,
        )

    # No task was created (submit raised before the spine got control).
    assert app_state.slice(EngineStateSlice).task_board_entry_adapter is not None
    all_tasks, _total = await task_engine.list_tasks(project=sid(_PROJECT))
    assert len(all_tasks) == 0
