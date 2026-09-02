"""Acceptance: stakes move the agent, never the horsepower.

Drives the REAL runtime through the production ``build_runtime_services``
(the exact code the boot hook runs) with a deterministic ``ScriptedDriver``
and a capability-priced provider catalogue, under the simulation harness
(zero real LLM spend). One brief decomposes into a low-stakes simple
subtask and a critical-stakes complex subtask.

An agent is a fixed ``(role, model)`` unit, so work that needs
more capability goes to a DIFFERENT AGENT. The acceptance is therefore two
things at once: which agent each subtask landed on, and which model each
call actually used. A cost total answers neither, because the same figure
is reachable by more than one mix of rungs.

The first test staffs both rungs and asserts the pairing: the critical
subtask goes to the expert-bound agent while the low-stakes one goes to the
CHEAPER agent even though the stronger one is idle and would score fine.
Preferring the exact rung is the org's standing cost discipline, and it is
what replaced budget auto-downgrade.

The second test staffs only the cheap rung. The critical subtask then has
nobody who may take it, so routing reports it unroutable and the run does
strictly less work. What it must never do is reach for a stronger model
under the same agent's name, which is precisely the call the assertion
refuses.
"""

from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path

import pytest

from synthorg.api.approval_store import ApprovalStore
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
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import (
    UNROUTABLE_ROLE_KEY,
    BlockedReason,
    Complexity,
    Priority,
    TaskStatus,
    TaskType,
)
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
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence.task_protocol import TaskFilterSpec
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

#: Comfortably above this brief's own task count, so a row is never paged
#: past and read as absent.
_TASK_PAGE = 50

_DECOMPOSITION_TOOL = "submit_decomposition_plan"
_DEBUG_SKILL = "debug"
_DATABASE_SKILL = "database"
_PROVIDER = "test-provider"

_CHEAP_SUBTASK_TITLE = "Tidy the log formatting"
_CRITICAL_SUBTASK_TITLE = "Migrate the production schema"

# The brief's definition of done, held here because the scripted plan has to
# claim these criteria VERBATIM: the parser matches a subtask's `satisfies`
# against the objective's own text, so two copies of the sentence would refuse
# the plan the moment one of them was reworded.
_LOG_CRITERION = "The log line is tidied without changing behaviour"
_MIGRATION_CRITERION = "The production schema migration is applied and verified"

# Path-shaped, so each subtask's declaration is one the workspace can be asked
# about and the scripted turn can satisfy by writing it. A prose declaration
# would be unprobeable, which is not an exemption from the delivery check but
# the one case where nothing else can answer it either.
_CHEAP_ARTIFACT = "logging/format.py"
_CRITICAL_ARTIFACT = "migrations/0001_schema.sql"

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
    """Price a completion by the capability embedded in *model_id*.

    Returns:
        The per-1k rate for that rung.
    """
    for capability, cost in _CAPABILITY_COST_PER_1K.items():
        if capability in model_id:
            return cost
    return _CAPABILITY_COST_PER_1K["expert"]


def _declared_artifact(messages: list[ChatMessage]) -> str:
    """Which subtask's declared path this turn is being asked to produce.

    The scripted turn has no task object, only the brief it was handed, so
    the subtask is read back off the title the decomposition wrote into it.

    Returns:
        The declared path for whichever subtask this session is running.
    """
    brief = "\n".join(str(message.content or "") for message in messages)
    if _CRITICAL_SUBTASK_TITLE in brief:
        return _CRITICAL_ARTIFACT
    return _CHEAP_ARTIFACT


class _MixedStakesStrategy:
    """Decompose into a low-stakes and a critical-stakes subtask.

    Prices every completion by the capability it is invoked with, and
    records the model id of each one. The recording is the point: the
    acceptance is which models the run called, and a cost total cannot
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
                                    "title": _CHEAP_SUBTASK_TITLE,
                                    "description": "Adjust logger output spacing.",
                                    "estimated_complexity": "simple",
                                    "stakes": "low",
                                    "required_role": "developer",
                                    "required_skills": [_DEBUG_SKILL],
                                    "acceptance_criteria": [
                                        "Log lines align consistently.",
                                    ],
                                    "satisfies": [_LOG_CRITERION],
                                    "expected_artifacts": [_CHEAP_ARTIFACT],
                                },
                                {
                                    "id": "sub-critical",
                                    "title": _CRITICAL_SUBTASK_TITLE,
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
                                    "satisfies": [_MIGRATION_CRITERION],
                                    "expected_artifacts": [_CRITICAL_ARTIFACT],
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
    """Build a provider exposing one model per rung.

    Returns:
        A single-connection catalogue the roster's bindings resolve against.
    """
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


def _agent(name: str, skill: str, capability: CapabilityLevel) -> AgentIdentity:
    """Build a roster agent bound to the *capability* rung.

    The binding is the operator's own choice, and it is the thing nothing in
    the loop may rewrite.

    Returns:
        An ACTIVE developer holding *skill*.
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
            model_id=_CAPABILITY_MODEL_IDS[capability],
            capability=capability,
        ),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
        # ``echo`` is ToolCategory.OTHER, which the default STANDARD level
        # excludes; the scripted turns call it, so it is allowed by name.
        # ``write_file`` rides beside it because a subtask that declares a
        # deliverable and leaves its workspace untouched is a no-op the engine
        # fails on purpose. This harness tests ROUTING, so the write is the
        # cheapest honest way to be a run that delivered rather than one
        # exempted from the check.
        tools=ToolPermissions(allowed=(NotBlankStr("echo"), NotBlankStr("write_file"))),
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
    agent_registry: AgentRegistryService,
    cost_tracker: CostTracker,
    driver_strategy: _MixedStakesStrategy,
) -> DefaultWorkPipeline:
    """Assemble the production runtime around *agent_registry*.

    Returns:
        The runtime's own work pipeline.
    """
    provider = ScriptedDriver(_PROVIDER, strategy=driver_strategy)
    registry = ProviderRegistry({_PROVIDER: provider})

    root_config = RootConfig(
        company_name="stakes-routing-e2e",
        providers=_provider_catalogue(),
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
        audit_log=AuditLog(),
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
    agent_registry: AgentRegistryService,
    project: str,
) -> tuple[float, tuple[str, ...]]:
    """Run the mixed-stakes brief against the roster.

    Returns:
        The total accrued cost and every model id the run's AGENT turns
        called, in order.
    """
    cost_tracker = CostTracker()
    driver_strategy = _MixedStakesStrategy()
    pipeline = await _build_pipeline(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        agent_registry=agent_registry,
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
        # capability routing, not the under-specified-work refinement path.
        acceptance_criteria=(_LOG_CRITERION, _MIGRATION_CRITERION),
    )
    result = await pipeline.run(work_item)
    assert result.verdict is RoutingVerdict.SPLITTABLE
    assert result.execution_path is ExecutionPath.TEAM
    assert result.is_success is True
    return (
        await cost_tracker.get_total_cost(),
        tuple(driver_strategy.agent_models_called),
    )


async def _roster(*agents: AgentIdentity) -> AgentRegistryService:
    """Register *agents* on a live roster.

    Returns:
        The roster the runtime reads, so a binding can be re-read after the
        run from the same place dispatch reads it.
    """
    registry = AgentRegistryService()
    for agent in agents:
        await registry.register(agent)
    return registry


async def _subtasks_by_title(
    persistence: FakePersistenceBackend,
    *,
    project: str,
) -> dict[str, Task]:
    """Read the project's persisted tasks, keyed by title.

    Returns:
        Every task row the run produced for *project*.
    """
    rows = await persistence.tasks.query(
        TaskFilterSpec(project=NotBlankStr(project)),
        limit=_TASK_PAGE,
    )
    return {row.title: row for row in rows}


async def test_the_critical_subtask_goes_to_a_stronger_agent(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """Stakes choose the agent; each agent runs the model it is bound to.

    Both rungs are staffed, so the ladder has a real choice to make. The
    critical subtask lands on the expert-bound DBA and the low-stakes one on
    the basic-bound debugger, and each call carries that agent's OWN model
    id. Under the deleted design the critical subtask ran on
    ``example-expert-001`` under the debugger's name, which shows up here as
    a call the pairing assertion refuses.

    The cheap subtask is the cost-discipline half: the expert agent is idle
    and would score perfectly well on it, and the exact rung still wins.
    """
    project = sid("proj-both-rungs")
    await persistence.projects.create(_project("proj-both-rungs"))
    debugger = _agent("debugger", _DEBUG_SKILL, "basic")
    dba = _agent("dba", _DATABASE_SKILL, "expert")
    roster = await _roster(debugger, dba)

    cost, models = await _run_brief(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        agent_registry=roster,
        project=project,
    )

    rows = await _subtasks_by_title(persistence, project=project)
    cheap = rows[_CHEAP_SUBTASK_TITLE]
    critical = rows[_CRITICAL_SUBTASK_TITLE]
    assert cheap.assigned_to == str(debugger.id)
    assert critical.assigned_to == str(dba.id)

    # Every call used a model some agent is actually bound to, and both
    # bindings were exercised: a swap would have put one agent's rung on the
    # other agent's work.
    assert set(models) == {
        _CAPABILITY_MODEL_IDS["basic"],
        _CAPABILITY_MODEL_IDS["expert"],
    }, models
    assert cost > 0.0

    # Re-read from the roster dispatch itself reads: routing moved the work,
    # and both bindings are exactly what the operator wrote.
    live_debugger = await roster.get(str(debugger.id))
    live_dba = await roster.get(str(dba.id))
    assert live_debugger is not None
    assert live_dba is not None
    assert live_debugger.model.model_id == _CAPABILITY_MODEL_IDS["basic"]
    assert live_dba.model.model_id == _CAPABILITY_MODEL_IDS["expert"]


async def test_an_understaffed_floor_refuses_rather_than_upgrades(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    """With nobody at the rung, the work is refused, never upgraded.

    Every agent here is bound to the cheap rung, so no one may take the
    critical subtask: routing reports it unroutable and it never reaches an
    agent. The answer the organisation owes an operator is an agent at the
    needed rung, not a stronger model behind an existing agent's name, so
    the run must never call one.
    """
    project = sid("proj-cheap-only")
    await persistence.projects.create(_project("proj-cheap-only"))

    cost, models = await _run_brief(
        persistence=persistence,
        task_engine=task_engine,
        tmp_path=tmp_path,
        agent_registry=await _roster(
            _agent("debugger", _DEBUG_SKILL, "basic"),
            _agent("dba", _DATABASE_SKILL, "basic"),
        ),
        project=project,
    )

    # The load-bearing assertion: no rung above the operator's own was ever
    # reached for, however consequential the subtask.
    assert set(models) == {_CAPABILITY_MODEL_IDS["basic"]}, models
    assert cost > 0.0

    # And the refusal is real rather than the subtask never existing: the
    # routable half ran, the unroutable half reached no agent. Coordination
    # files every decomposed child BEFORE routing, so the row is always
    # there; what marks it refused is that nobody was assigned.
    #
    # Parked, not left in the backlog. A row sitting CREATED with no assignee
    # is assignable in principle and watched by nothing in practice, so the
    # refusal would be invisible to the operator who is owed a hire. The park
    # names both halves of what it is waiting on: the reason, and the role the
    # planner asked for, which is recoverable here and nowhere downstream.
    # That pair is exactly what ``unroutable_by_role`` sweeps, so asserting it
    # is asserting the refusal has a way out.
    rows = await _subtasks_by_title(persistence, project=project)
    assert rows[_CHEAP_SUBTASK_TITLE].assigned_to is not None
    critical = rows[_CRITICAL_SUBTASK_TITLE]
    assert critical.assigned_to is None
    assert critical.status is TaskStatus.BLOCKED
    assert critical.blocked_reason is BlockedReason.NO_CAPABLE_AGENT
    assert critical.metadata[UNROUTABLE_ROLE_KEY] == "developer"
