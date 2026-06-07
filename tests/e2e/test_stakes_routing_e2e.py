"""Acceptance: stakes-aware routing cuts cost on a mixed-stakes brief.

Drives the REAL runtime through the production ``build_runtime_services``
(the exact code the boot hook runs) with a deterministic
``ScriptedDriver`` and a tier-priced provider catalogue, under the
simulation harness (zero real LLM spend). A single brief decomposes into
a low-stakes subtask and a critical-stakes subtask; the same brief is run
twice, once with the ``stakes_aware`` routing strategy and once with the
``flat`` control arm.

The acceptance for #1998 is that, on a mixed brief, cheap models handle
low-stakes subtasks and strong models handle high/critical ones, so total
cost drops versus flat routing at no quality-floor regression. The
scripted driver prices each completion by the model tier it is called
with, so the cost the ``CostTracker`` accrues reflects the tier the
router selected: stakes-aware routes the low-stakes subtask down to the
cheap tier while flat keeps every subtask on the agent's configured large
tier, so the stakes-aware run costs strictly less.
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.budget.benchmark_stub import StubBenchmarkScoreProvider
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.tracker import CostTracker
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.enums import AgentStatus, Complexity, Priority, TaskType
from synthorg.core.role import Authority, Skill
from synthorg.core.types import ModelTier
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.intake.models import IntakeResult
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkSource,
)
from synthorg.engine.pipeline.service import DefaultWorkPipeline
from synthorg.engine.routing_policy.config import StakesRoutingConfig
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
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
from tests._shared import FakeClock, make_app_state, mock_of
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_DECOMPOSITION_TOOL = "submit_decomposition_plan"
_DEBUG_SKILL = "debug"
_DATABASE_SKILL = "database"
_PROVIDER = "test-provider"

# Tier-priced model catalogue. Model ids carry the tier token so the
# StubBenchmarkScoreProvider scores them (small 72, medium 85, large 92)
# and the scripted driver can price each completion by tier.
_TIER_MODEL_IDS: dict[ModelTier, str] = {
    "small": "example-small-001",
    "medium": "example-medium-001",
    "large": "example-large-001",
}
_TIER_COST_PER_1K: dict[ModelTier, float] = {
    "small": 0.001,
    "medium": 0.005,
    "large": 0.02,
}


def _cost_for_model(model_id: str) -> float:
    """Price a completion by the tier embedded in *model_id*."""
    for tier, cost in _TIER_COST_PER_1K.items():
        if tier in model_id:
            return cost
    return _TIER_COST_PER_1K["large"]


class _MixedStakesStrategy:
    """Decompose into a low-stakes and a critical-stakes subtask.

    Prices every completion by the model tier it is invoked with, so the
    accrued cost reflects the router's tier choice.
    """

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        del messages, config
        cost = _cost_for_model(model)
        usage = TokenUsage(input_tokens=8, output_tokens=4, cost=cost)
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
                                    "id": "sub-cheap",
                                    "title": "Tidy the log formatting",
                                    "description": "Adjust logger output spacing.",
                                    "estimated_complexity": "simple",
                                    "required_skills": [_DEBUG_SKILL],
                                },
                                {
                                    "id": "sub-critical",
                                    "title": "Migrate the production schema",
                                    "description": (
                                        "Run an irreversible production "
                                        "migration of the live schema."
                                    ),
                                    "estimated_complexity": "complex",
                                    "required_skills": [_DATABASE_SKILL],
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
            task_id=created.id,
        )


def _provider_catalogue() -> dict[str, ProviderConfig]:
    """A single provider exposing the three tier aliases the router uses."""
    return {
        _PROVIDER: ProviderConfig(
            driver="scripted",
            models=tuple(
                ProviderModelConfig(
                    id=_TIER_MODEL_IDS[tier],
                    alias=tier,
                    cost_per_1k_input=_TIER_COST_PER_1K[tier],
                    cost_per_1k_output=_TIER_COST_PER_1K[tier],
                    max_context=128000,
                )
                for tier in _TIER_MODEL_IDS
            ),
        ),
    }


def _large_tier_agent(name: str, skill: str) -> AgentIdentity:
    """An agent configured on the large tier (flat keeps it there)."""
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        skills=SkillSet(primary=(Skill(id=skill, name=skill),)),
        authority=Authority(budget_limit=100.0),
        model=ModelConfig(
            provider=_PROVIDER,
            model_id=_TIER_MODEL_IDS["large"],
            model_tier="large",
        ),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


def _project(project_id: str) -> Any:
    from synthorg.core.enums import ProjectStatus
    from synthorg.core.project import Project

    return Project(
        id=project_id,
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
    stakes_strategy: str,
    cost_tracker: CostTracker,
) -> DefaultWorkPipeline:
    provider = ScriptedDriver(_PROVIDER, strategy=_MixedStakesStrategy())
    registry = ProviderRegistry({_PROVIDER: provider})
    agent_registry = AgentRegistryService()
    for agent in (
        _large_tier_agent("debugger", _DEBUG_SKILL),
        _large_tier_agent("dba", _DATABASE_SKILL),
    ):
        await agent_registry.register(agent)

    root_config = RootConfig(
        company_name="stakes-routing-e2e",
        coordination_metrics=CoordinationMetricsConfig(enabled=True),
        providers=_provider_catalogue(),
        stakes_routing=StakesRoutingConfig(strategy=stakes_strategy),
    )
    settings_service = SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
    )
    await settings_service.set("coordination", "routing_policy", "always-team")
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
        benchmark_provider=StubBenchmarkScoreProvider(),
        cost_tracker=cost_tracker,
    )
    runtime = await build_runtime_services(app_state, workspace_root=tmp_path)
    pipeline = runtime.work_pipeline
    assert isinstance(pipeline, DefaultWorkPipeline)
    return pipeline


async def _run_brief(
    *,
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
    stakes_strategy: str,
    project: str,
) -> float:
    """Run the mixed-stakes brief and return the total accrued cost."""
    cost_tracker = CostTracker()
    pipeline = await _build_pipeline(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        stakes_strategy=stakes_strategy,
        cost_tracker=cost_tracker,
    )
    work_item = WorkItem(
        origin_adapter_id="harness",
        source=WorkSource.SIMULATION,
        title="Production incident remediation",
        raw_intent="Tidy a log line and migrate the production schema.",
        project=project,
        requested_by="operator",
    )
    result = await pipeline.run(work_item)
    assert result.verdict is RoutingVerdict.SPLITTABLE
    assert result.execution_path is ExecutionPath.TEAM
    assert result.is_success is True
    return await cost_tracker.get_total_cost()


async def test_stakes_aware_costs_less_than_flat_on_mixed_brief(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """Stakes-aware routing accrues strictly less cost than the flat arm.

    The flat arm keeps every subtask on the agent's configured large
    tier; stakes-aware routes the low-stakes subtask down to the cheap
    tier, so the same brief costs less end-to-end with no quality-floor
    regression (each selected tier still clears its per-stakes floor).
    """
    await persistence.projects.create(_project("proj-aware"))
    await persistence.projects.create(_project("proj-flat"))

    aware_cost = await _run_brief(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        stakes_strategy="stakes_aware",
        project="proj-aware",
    )
    flat_cost = await _run_brief(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        stakes_strategy="flat",
        project="proj-flat",
    )

    assert aware_cost > 0.0
    assert flat_cost > 0.0
    assert aware_cost < flat_cost
