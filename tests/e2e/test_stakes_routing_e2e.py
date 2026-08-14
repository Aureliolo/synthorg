"""Acceptance: stakes routing moves the agent, never the horsepower.

Drives the REAL runtime through the production ``build_runtime_services``
(the exact code the boot hook runs) with a deterministic
``ScriptedDriver`` and a capability-priced provider catalogue, under the
simulation harness (zero real LLM spend). A single brief decomposes into
a low-stakes subtask and a critical-stakes subtask; the same brief is run
twice, once with the ``stakes_aware`` routing strategy and once with the
``flat`` control arm.

Stakes set a capability FLOOR on the agent that may take the subtask;
they never re-point an agent's own binding at a different model. So the
acceptance is what each arm CALLS, not what it spends: the scripted
driver records the model id of every completion, and on a roster where
every agent is bound to the cheap rung, neither arm may ever call
anything else. The old design would have answered the critical subtask by
substituting a stronger model under the same agent's name, which shows up
here as a call this assertion refuses.

What stakes-aware does instead, when no agent on the roster clears the
floor, is park the subtask for a human and say why
(``StakesModelUnavailableError``). That is the honest outcome and it is
why the stakes-aware arm makes strictly fewer calls than the flat arm,
which has no floor and runs the critical subtask on the cheap rung the
operator chose.
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.tracker import CostTracker
from synthorg.client.models import ClientRequest
from synthorg.client.simulation_state import ClientSimulationState
from synthorg.config.provider_schema import ProviderConfig, ProviderModelConfig
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
from synthorg.core.task import AcceptanceCriterion
from synthorg.core.task_enums import Complexity, Priority, TaskType
from synthorg.core.types import CapabilityLevel, NotBlankStr
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
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.workers.runtime_builder import build_runtime_services
from tests._shared import (
    FakeCapabilityBenchmarkScoreProvider,
    FakeClock,
    as_uuid,
    make_app_state,
    mock_of,
    sid,
    wire_decomposition_model,
)
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

#: Well above the one park this brief can produce, so a second would be
#: read and fail the count rather than paged past.
_PARK_PAGE = 50

_DECOMPOSITION_TOOL = "submit_decomposition_plan"
_DEBUG_SKILL = "debug"
_DATABASE_SKILL = "database"
_PROVIDER = "test-provider"

# Capability-priced model catalogue. Model ids are the canonical
# ``example-<capability>`` archetypes, so the heuristic classifier assigns each
# its rung, and the scripted driver can price each completion by rung.
_CAPABILITY_MODEL_IDS: dict[CapabilityLevel, str] = {
    "basic": "example-basic-001",
    "capable": "example-capable-001",
    "expert": "example-expert-001",
}
_CAPABILITY_COST_PER_1K: dict[CapabilityLevel, float] = {
    "basic": 0.001,
    "capable": 0.005,
    "expert": 0.02,
}


def _cost_for_model(model_id: str) -> float:
    """Price a completion by the capability embedded in *model_id*."""
    for capability, cost in _CAPABILITY_COST_PER_1K.items():
        if capability in model_id:
            return cost
    return _CAPABILITY_COST_PER_1K["expert"]


class _MixedStakesStrategy:
    """Decompose into a low-stakes and a critical-stakes subtask.

    Prices every completion by the capability it is invoked with, and
    records the model id of each one. The recording is the point: the
    acceptance is which models the arm called, and a cost total cannot
    answer that, because the same figure can be reached by a different
    mix of rungs. A sub-agent turn calls one tool before answering,
    because a run that declares deliverables and calls nothing is a
    silent no-op the engine fails on purpose.
    """

    def __init__(self) -> None:
        self.agent_models_called: list[str] = []

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        del config
        cost = _cost_for_model(model)
        usage = TokenUsage(input_tokens=8, output_tokens=4, cost=cost)
        is_decomposition = tools is not None and any(
            t.name == _DECOMPOSITION_TOOL for t in tools
        )
        if not is_decomposition:
            # Only agent turns are recorded. Decomposition runs on the
            # coordinator's own MODEL_REF binding, a system feature with
            # no agent behind it, so counting it would put a model the
            # roster never chose into an assertion about roster bindings.
            self.agent_models_called.append(model)
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
                                    "stakes": "low",
                                    "required_role": "developer",
                                    "required_skills": [_DEBUG_SKILL],
                                    "acceptance_criteria": [
                                        "Log lines align consistently.",
                                    ],
                                    # Prose, not a path: this harness runs no
                                    # real editor, and the artifact probe asks
                                    # the workspace only about path-shaped
                                    # declarations.
                                    "expected_artifacts": [
                                        "log lines that align consistently"
                                    ],
                                },
                                {
                                    "id": "sub-critical",
                                    "title": "Migrate the production schema",
                                    "description": (
                                        "Run an irreversible production "
                                        "migration of the live schema."
                                    ),
                                    "estimated_complexity": "complex",
                                    "stakes": "critical",
                                    "required_role": "developer",
                                    "required_skills": [_DATABASE_SKILL],
                                    "acceptance_criteria": [
                                        "The schema migrates without data loss.",
                                    ],
                                    "expected_artifacts": [
                                        "a migrated production schema"
                                    ],
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
                    ToolCall(id="work-1", name="echo", arguments={"message": "done"}),
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


def _provider_catalogue() -> dict[str, ProviderConfig]:
    """A single provider exposing the three rung aliases the router uses."""
    return {
        _PROVIDER: ProviderConfig(
            connection_name="conn-test",
            driver="scripted",
            models=tuple(
                ProviderModelConfig(
                    id=_CAPABILITY_MODEL_IDS[capability],
                    alias=capability,
                    cost_per_1k_input=_CAPABILITY_COST_PER_1K[capability],
                    cost_per_1k_output=_CAPABILITY_COST_PER_1K[capability],
                    max_context=128000,
                )
                for capability in _CAPABILITY_MODEL_IDS
            ),
        ),
    }


def _basic_capability_agent(name: str, skill: str) -> AgentIdentity:
    """An agent whose roster binding is the cheap rung.

    Both arms start from the operator's own choice. ``flat`` keeps it for
    every subtask; ``stakes_aware`` keeps it too, except where the stakes
    floor is above it, which is the only reason it may move.
    """
    return AgentIdentity(
        id=as_uuid(name),
        name=name,
        role="developer",
        department="engineering",
        skills=SkillSet(primary=(Skill(id=skill, name=skill),)),
        authority=Authority(budget_limit=100.0),
        model=ModelConfig(
            provider=_PROVIDER,
            model_id=_CAPABILITY_MODEL_IDS["basic"],
            capability="basic",
        ),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
        # ``echo`` is ToolCategory.OTHER, which the default STANDARD level
        # excludes; the scripted turns call it, so it is allowed by name.
        tools=ToolPermissions(allowed=(NotBlankStr("echo"),)),
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
    stakes_strategy: str,
    cost_tracker: CostTracker,
    driver_strategy: _MixedStakesStrategy,
) -> DefaultWorkPipeline:
    provider = ScriptedDriver(_PROVIDER, strategy=driver_strategy)
    registry = ProviderRegistry({_PROVIDER: provider})
    agent_registry = AgentRegistryService()
    for agent in (
        _basic_capability_agent("debugger", _DEBUG_SKILL),
        _basic_capability_agent("dba", _DATABASE_SKILL),
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
        clock=FakeClock(),
        agent_workspace_root=tmp_path,
        persistence=persistence,
        client_simulation_state=mock_of[ClientSimulationState](
            intake_engine=intake,
        ),
        benchmark_provider=FakeCapabilityBenchmarkScoreProvider(),
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
) -> tuple[float, tuple[str, ...]]:
    """Run the mixed-stakes brief.

    Returns:
        The total accrued cost and every model id the arm's AGENT turns
        called, in order. The second half is what the acceptance reads: a
        cost can be reached by more than one mix of rungs, a call list
        cannot.
    """
    cost_tracker = CostTracker()
    driver_strategy = _MixedStakesStrategy()
    pipeline = await _build_pipeline(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        stakes_strategy=stakes_strategy,
        cost_tracker=cost_tracker,
        driver_strategy=driver_strategy,
    )
    work_item = WorkItem(
        origin_adapter_id="harness",
        source=WorkSource.SIMULATION,
        title="Production incident remediation",
        raw_intent="Tidy a log line and migrate the production schema.",
        project=project,
        requested_by="operator",
        # A definition of done so the coordinator's clarification gate (on
        # by default) passes and the team path runs; this test exercises
        # stakes-aware routing, not the under-specified-work refinement path.
        acceptance_criteria=(
            "The log line is tidied without changing behaviour",
            "The production schema migration is applied and verified",
        ),
    )
    result = await pipeline.run(work_item)
    assert result.verdict is RoutingVerdict.SPLITTABLE
    assert result.execution_path is ExecutionPath.TEAM
    assert result.is_success is True
    return (
        await cost_tracker.get_total_cost(),
        tuple(driver_strategy.agent_models_called),
    )


async def test_stakes_routes_the_agent_and_never_swaps_the_model(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """Stakes set a floor on the agent; they never re-point its binding.

    Every agent on this roster is bound to the cheap rung, so a design
    that answered critical stakes by reaching for a stronger model would
    show up as a call to one. Neither arm may make that call.

    The floor still bites, it just bites the assignment: no agent clears
    ``expert``, so stakes-aware parks the critical subtask for a human
    rather than running it on a model that cannot do it. The flat arm has
    no floor and runs it on the operator's cheap rung, which is why it
    makes strictly more calls.
    """
    await persistence.projects.create(_project("proj-aware"))
    await persistence.projects.create(_project("proj-flat"))

    aware_cost, aware_models = await _run_brief(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        stakes_strategy="stakes_aware",
        project=sid("proj-aware"),
    )
    flat_cost, flat_models = await _run_brief(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        stakes_strategy="flat",
        project=sid("proj-flat"),
    )

    basic = _CAPABILITY_MODEL_IDS["basic"]
    # The load-bearing assertion. Under the deleted design the critical
    # subtask ran on example-expert-001 under the same agent's name.
    assert set(aware_models) == {basic}, aware_models
    assert set(flat_models) == {basic}, flat_models

    # The floor refused the critical subtask rather than upgrading it, so
    # the stakes-aware arm did strictly less work, not more expensive work.
    assert aware_cost > 0.0
    assert flat_cost > 0.0
    assert len(aware_models) < len(flat_models)

    # A call count alone cannot tell "the floor refused it" from "the
    # subtask was never created", and the two differ by everything: the
    # first parks with a reason an operator can act on, the second is work
    # quietly missing. Read the park.
    parked = await persistence.parked_contexts.list_items(limit=_PARK_PAGE)
    refusals = [
        row
        for row in parked
        if row.metadata.get("action_type") == "stakes:model_unavailable"
    ]
    assert len(refusals) == 1, [row.metadata for row in parked]
    assert refusals[0].task_id is not None
