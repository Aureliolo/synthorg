"""Acceptance: a real submitted objective executes via the pipeline.

Builds the REAL runtime through the production
``build_runtime_services`` (the exact code the boot hook runs) with a
deterministic ``ScriptedDriver``. The real ``ObjectiveEntryAdapter``
drives an :class:`ObjectiveSubmission` through the spine: intake ->
projects -> decompose -> solo / team execution. The spawned root task
reaches a post-execution status (proof an agent actually ran). The
second case forces the ``ALWAYS_TEAM`` routing policy so the
discriminating ``ExecutionPath.TEAM`` assertion proves the objective
genuinely decomposed via the multi-agent coordinator rather than
running solo.

Zero real LLM spend: the scripted provider returns a decomposition
plan when the LLM is invoked for decomposition (tool call), and a
plain STOP completion for every agent turn.
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.budget.tracker import CostTracker
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.role import Authority, Skill
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.strategies import DirectIntake
from synthorg.engine.pipeline.entry.boot import _project_uuid
from synthorg.engine.pipeline.entry.objective_adapter import (
    ObjectiveEntryAdapter,
    ObjectiveSubmission,
)
from synthorg.engine.pipeline.models import ExecutionPath, RoutingVerdict
from synthorg.engine.task_engine import TaskEngine
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

_PROJECT = "objectives"
# Canonical project id the production boot derives from the slug; the
# entry adapter, intake strategy, and seed all key off this so the
# pipeline's ``projects.get(work_item.project)`` resolves exactly as it
# does in production.
_PROJECT_ID = str(_project_uuid(_PROJECT))
_RESEARCH_SKILL = "research"
_DECOMPOSITION_TOOL = "submit_decomposition_plan"


class _StopStrategy:
    """Branches decomposition tool calls vs plain sub-agent turns.

    The multi-agent coordinator's decomposition stage invokes the LLM
    with a ``submit_decomposition_plan`` tool definition; the strategy
    returns a structured single-subtask plan so the decomposer parses
    a valid response. Every other turn (agent execution) returns a
    plain STOP completion so the worker execution service drives the
    subtask past ASSIGNED through to a post-execution status.
    """

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        del messages, config
        usage = TokenUsage(input_tokens=8, output_tokens=4, cost=0.0)
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
                            "task_structure": "sequential",
                            "coordination_topology": "centralized",
                            "subtasks": [
                                {
                                    "id": "sub-research",
                                    "title": "Research release scope",
                                    "description": (
                                        "Investigate the work the objective describes."
                                    ),
                                    "required_skills": [_RESEARCH_SKILL],
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


async def _build_objective_adapter(
    *,
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
    routing_policy: str,
) -> ObjectiveEntryAdapter:
    """Wire the production runtime and return the real objective adapter."""
    await persistence.projects.create(
        Project(
            id=_project_uuid(_PROJECT),
            name=_PROJECT,
            description="real objective e2e",
            status=ProjectStatus.ACTIVE,
        )
    )
    provider = ScriptedDriver("test-provider", strategy=_StopStrategy())
    registry = ProviderRegistry({"test-provider": provider})
    agent_registry = AgentRegistryService()
    await agent_registry.register(_make_agent())

    root_config = RootConfig(company_name="real-objective-e2e")
    settings_service = SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
    )
    await settings_service.set("coordination", "routing_policy", routing_policy)
    config_resolver = ConfigResolver(
        settings_service=settings_service,
        config=root_config,
    )
    from synthorg.engine.review.pipeline import ReviewPipeline

    sim_state = ClientSimulationState(
        intake_engine=IntakeEngine(
            strategy=DirectIntake(task_engine=task_engine, project=_PROJECT_ID),
        ),
        # An empty ReviewPipeline (no stages) keeps ``has_simulation_runtime``
        # truthy without exercising review logic the objective-entry path
        # does not touch; without this, the work-pipeline build short-
        # circuits to ``None`` and every objective-entry assertion fails.
        review_pipeline=ReviewPipeline(stages=()),
        intake_default_project=_PROJECT_ID,
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
    return ObjectiveEntryAdapter(
        work_pipeline=runtime.work_pipeline,
        default_project=_PROJECT_ID,
    )


async def test_objective_executes_through_pipeline_under_default_policy(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """An objective routes through the spine and the spawned task runs.

    Under the shipped leaf-threshold policy the objective may classify
    LEAF (solo agent) or SPLITTABLE (team coordination) depending on
    its inferred structure; either outcome is acceptable here. The
    pipeline result must succeed and the spawned task must reach a
    post-execution status.
    """
    adapter = await _build_objective_adapter(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        routing_policy="leaf-threshold",
    )
    submission = ObjectiveSubmission(
        title="Add a status endpoint",
        description="Return a JSON status body from /status.",
        requested_by="human-operator",
    )

    result = await adapter.submit(submission)

    assert result.is_success
    assert result.task_id is not None
    persisted = await task_engine.get_task(result.task_id)
    assert persisted is not None
    assert persisted.status is not TaskStatus.CREATED
    assert persisted.project == _PROJECT_ID


async def test_objective_decomposes_under_always_team_policy(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """Discriminating check: objective genuinely decomposes via coordinator.

    Forces ``always-team`` so the routing policy guarantees ``SPLITTABLE``
    irrespective of structure heuristics. The pipeline must take the
    ``TEAM`` path, the multi-agent coordinator must decompose and
    execute, and the root task must reach a post-execution status.
    """
    adapter = await _build_objective_adapter(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        routing_policy="always-team",
    )
    submission = ObjectiveSubmission(
        title="Ship the v0.8 release",
        description="Cut a stable v0.8 release with release notes.",
        requested_by="human-operator",
    )

    result = await adapter.submit(submission)

    assert result.is_success
    assert result.verdict is RoutingVerdict.SPLITTABLE
    assert result.execution_path is ExecutionPath.TEAM
    assert result.task_id is not None
    persisted = await task_engine.get_task(result.task_id)
    assert persisted is not None
    assert persisted.status is not TaskStatus.CREATED
    assert persisted.project == _PROJECT_ID
