"""Unit test configuration and fixtures for engine modules."""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    SkillSet,
)
from synthorg.core.company import Company, CompanyConfig, Department
from synthorg.core.enums import (
    AgentStatus,
    Complexity,
    CoordinationTopology,
    CreativityLevel,
    DepartmentName,
    Priority,
    RiskTolerance,
    SeniorityLevel,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from synthorg.core.role import Authority, Role
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.engine.context import AgentContext
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.parallel_models import (
    AgentOutcome,
    ParallelExecutionResult,
)
from synthorg.engine.routing.models import (
    RoutingCandidate,
    RoutingDecision,
    RoutingResult,
)
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.engine.task_execution import TaskExecution
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)
from tests.unit.engine.task_engine_helpers import FakeMessageBus, FakePersistence

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from synthorg.core.enums import ConflictEscalation
    from synthorg.engine.workspace.models import (
        MergeConflict,
        MergeResult,
        Workspace,
    )


@pytest.fixture
def sample_model_config() -> ModelConfig:
    """Vendor-agnostic model config for prompt testing."""
    return ModelConfig(provider="test-provider", model_id="test-model-001")


@pytest.fixture
def sample_agent_with_personality(sample_model_config: ModelConfig) -> AgentIdentity:
    """Agent with rich personality config for prompt testing."""
    return AgentIdentity(
        id=uuid4(),
        name="Ada Lovelace",
        role="Senior Backend Developer",
        department="Engineering",
        level=SeniorityLevel.SENIOR,
        personality=PersonalityConfig(
            traits=("analytical", "methodical", "detail-oriented"),
            communication_style="concise and technical",
            risk_tolerance=RiskTolerance.LOW,
            creativity=CreativityLevel.HIGH,
            description="A precise thinker who values correctness above all.",
        ),
        skills=SkillSet(
            primary=("python", "system-design"),
            secondary=("databases", "security"),
        ),
        authority=Authority(
            can_approve=("code_reviews", "design_docs"),
            reports_to="engineering_lead",
            can_delegate_to=("mid_developers",),
            budget_limit=10.0,
        ),
        model=sample_model_config,
        hiring_date=date(2026, 1, 15),
    )


@pytest.fixture
def sample_role_with_description() -> Role:
    """Role with description for prompt rendering tests."""
    return Role(
        name="Senior Backend Developer",
        department=DepartmentName.ENGINEERING,
        required_skills=("python", "apis"),
        authority_level=SeniorityLevel.SENIOR,
        description="Designs and implements backend services and APIs.",
    )


@pytest.fixture
def sample_task_with_criteria(
    sample_agent_with_personality: AgentIdentity,
) -> Task:
    """Task with acceptance criteria and budget for prompt testing."""
    return Task(
        id="task-prompt-001",
        title="Implement authentication module",
        description="Create JWT-based authentication for the REST API.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="proj-001",
        created_by="product_manager",
        acceptance_criteria=(
            AcceptanceCriterion(description="Login endpoint returns JWT token"),
            AcceptanceCriterion(description="Token refresh works correctly"),
        ),
        estimated_complexity=Complexity.MEDIUM,
        budget_limit=5.0,
        deadline="2026-04-01T00:00:00",
        assigned_to=str(sample_agent_with_personality.id),
        status=TaskStatus.ASSIGNED,
    )


@pytest.fixture
def sample_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Tuple of tool definitions for prompt testing."""
    return (
        ToolDefinition(
            name="code_search",
            description="Search the codebase for patterns and symbols.",
        ),
        ToolDefinition(
            name="run_tests",
            description="Execute the project test suite.",
        ),
        ToolDefinition(
            name="file_editor",
            description="Read and edit source files.",
        ),
    )


@pytest.fixture
def sample_token_usage() -> TokenUsage:
    """Small token usage for testing cost accumulation."""
    return TokenUsage(
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
    )


@pytest.fixture
def sample_task_execution(sample_task_with_criteria: Task) -> TaskExecution:
    """TaskExecution from sample task (starts at ASSIGNED)."""
    return TaskExecution.from_task(sample_task_with_criteria)


@pytest.fixture
def sample_agent_context(
    sample_agent_with_personality: AgentIdentity,
    sample_task_with_criteria: Task,
) -> AgentContext:
    """AgentContext from sample agent + sample task."""
    return AgentContext.from_identity(
        sample_agent_with_personality,
        task=sample_task_with_criteria,
    )


@pytest.fixture
def sample_company() -> Company:
    """Company with departments for prompt testing."""
    return Company(
        name="Acme AI Corp",
        departments=(
            Department(name="Engineering", head="cto", budget_percent=50.0),
            Department(name="Product", head="cpo", budget_percent=20.0),
        ),
        config=CompanyConfig(budget_monthly=500.0),
    )


class MockCompletionProvider:
    """Test double for ``CompletionProvider``.

    Pops the next response from a pre-configured list on each
    ``complete()`` call.  Raises ``IndexError`` if called more times
    than there are responses.
    """

    def __init__(self, responses: list[CompletionResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        self._recorded_configs: list[CompletionConfig | None] = []
        self._recorded_models: list[str] = []
        self._recorded_messages: list[list[ChatMessage]] = []
        self._recorded_tools: list[list[ToolDefinition] | None] = []

    @property
    def call_count(self) -> int:
        """Number of ``complete()`` calls made."""
        return self._call_count

    @property
    def recorded_configs(self) -> list[CompletionConfig | None]:
        """Configs passed to each ``complete()`` call."""
        return list(self._recorded_configs)

    @property
    def recorded_models(self) -> list[str]:
        """Models passed to each ``complete()`` call."""
        return list(self._recorded_models)

    @property
    def recorded_messages(self) -> list[list[ChatMessage]]:
        """Messages passed to each ``complete()`` call."""
        return [list(m) for m in self._recorded_messages]

    @property
    def recorded_tools(self) -> list[list[ToolDefinition] | None]:
        """Tools passed to each ``complete()`` call."""
        return [list(t) if t is not None else None for t in self._recorded_tools]

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Return the next pre-configured response."""
        if not self._responses:
            msg = "MockCompletionProvider: no more responses"
            raise IndexError(msg)
        self._call_count += 1
        self._recorded_configs.append(config)
        self._recorded_models.append(model)
        self._recorded_messages.append(list(messages))
        self._recorded_tools.append(list(tools) if tools is not None else None)
        return self._responses.pop(0)

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        msg = "MockCompletionProvider.stream() is not implemented"
        raise NotImplementedError(msg)

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        """Return minimal capabilities."""
        return ModelCapabilities(
            model_id=model,
            provider="test-provider",
            supports_tools=True,
            supports_streaming=False,
            max_context_tokens=8192,
            max_output_tokens=4096,
            cost_per_1k_input=0.01,
            cost_per_1k_output=0.03,
        )


def make_completion_response(
    *,
    content: str = "Done.",
    finish_reason: FinishReason = FinishReason.STOP,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: float = 0.01,
) -> CompletionResponse:
    """Build a simple CompletionResponse for tests."""
    return CompletionResponse(
        content=content,
        finish_reason=finish_reason,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        ),
        model="test-model-001",
    )


@pytest.fixture
def mock_provider_factory() -> type[MockCompletionProvider]:
    """Expose MockCompletionProvider class for test construction."""
    return MockCompletionProvider


# ---------------------------------------------------------------------------
# Workspace helpers (shared across workspace test files)
# ---------------------------------------------------------------------------

_DEFAULT_CREATED_AT = datetime(2026, 3, 8, tzinfo=UTC)


def make_workspace(  # noqa: PLR0913
    *,
    workspace_id: str = "ws-001",
    task_id: str = "task-1",
    agent_id: str = "agent-1",
    branch_name: str = "workspace/task-1",
    worktree_path: str = "fake/worktrees/ws-001",
    base_branch: str = "main",
    created_at: datetime | None = None,
) -> Workspace:
    """Build a ``Workspace`` with sensible defaults."""
    from synthorg.engine.workspace.models import Workspace

    return Workspace(
        workspace_id=workspace_id,
        task_id=task_id,
        agent_id=agent_id,
        branch_name=branch_name,
        worktree_path=worktree_path,
        base_branch=base_branch,
        created_at=created_at or _DEFAULT_CREATED_AT,
    )


def make_merge_result(  # noqa: PLR0913
    *,
    workspace_id: str = "ws-001",
    branch_name: str = "workspace/task-1",
    success: bool = True,
    conflicts: tuple[MergeConflict, ...] = (),
    duration_seconds: float = 0.5,
    merged_commit_sha: str | None = None,
    escalation: ConflictEscalation | None = None,
) -> MergeResult:
    """Build a ``MergeResult`` with sensible defaults."""
    from synthorg.engine.workspace.models import MergeResult

    if merged_commit_sha is None and success:
        merged_commit_sha = "abc123"

    return MergeResult(
        workspace_id=workspace_id,
        branch_name=branch_name,
        success=success,
        conflicts=conflicts,
        duration_seconds=duration_seconds,
        merged_commit_sha=merged_commit_sha,
        escalation=escalation,
    )


# ── Assignment strategy test helpers ─────────────────────────


def make_assignment_model_config() -> ModelConfig:
    """Build a vendor-agnostic ModelConfig for assignment tests."""
    return ModelConfig(
        provider="test-provider",
        model_id="test-small-001",
    )


def make_assignment_agent(  # noqa: PLR0913
    name: str,
    *,
    level: SeniorityLevel = SeniorityLevel.MID,
    primary_skills: tuple[str, ...] = (),
    secondary_skills: tuple[str, ...] = (),
    role: str = "Developer",
    status: AgentStatus = AgentStatus.ACTIVE,
) -> AgentIdentity:
    """Build an AgentIdentity with sensible defaults for tests."""
    return AgentIdentity(
        name=name,
        role=role,
        department="Engineering",
        level=level,
        model=make_assignment_model_config(),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(
            primary=primary_skills,
            secondary=secondary_skills,
        ),
        status=status,
    )


def make_assignment_task(**overrides: object) -> Task:
    """Build a Task with sensible defaults for assignment tests."""
    defaults: dict[str, object] = {
        "id": "task-001",
        "title": "Test task",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "project": "proj-001",
        "created_by": "manager",
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


# ── TaskEngine fixtures ───────────────────────────────────────


@pytest.fixture
def persistence() -> FakePersistence:
    """Provide a fresh FakePersistence instance."""
    return FakePersistence()


@pytest.fixture
def message_bus() -> FakeMessageBus:
    """Provide a fresh FakeMessageBus instance."""
    return FakeMessageBus()


@pytest.fixture
def config() -> TaskEngineConfig:
    """Provide a TaskEngineConfig with a sensible queue size."""
    return TaskEngineConfig(max_queue_size=100)


@pytest.fixture
async def engine(
    persistence: FakePersistence,
    config: TaskEngineConfig,
) -> AsyncIterator[TaskEngine]:
    """Create and start a TaskEngine, stop on teardown."""
    eng = TaskEngine(
        persistence=persistence,  # type: ignore[arg-type]
        config=config,
    )
    eng.start()
    yield eng
    await eng.stop(timeout=2.0)


@pytest.fixture
async def engine_with_bus(
    persistence: FakePersistence,
    message_bus: FakeMessageBus,
    config: TaskEngineConfig,
) -> AsyncIterator[TaskEngine]:
    """Create and start a TaskEngine with a message bus."""
    await message_bus.start()
    eng = TaskEngine(
        persistence=persistence,  # type: ignore[arg-type]
        message_bus=message_bus,  # type: ignore[arg-type]
        config=config,
    )
    eng.start()
    yield eng
    await eng.stop(timeout=2.0)
    await message_bus.stop()


# ---------------------------------------------------------------------------
# Coordination helpers (shared across coordination test files)
# ---------------------------------------------------------------------------


def make_subtask(
    subtask_id: str,
    *,
    dependencies: tuple[str, ...] = (),
) -> SubtaskDefinition:
    """Build a SubtaskDefinition with defaults."""
    return SubtaskDefinition(
        id=subtask_id,
        title=f"Subtask {subtask_id}",
        description=f"Description for {subtask_id}",
        dependencies=dependencies,
    )


def make_decomposition(
    subtasks: tuple[SubtaskDefinition, ...],
    *,
    parent_task_id: str = "parent-1",
    topology: CoordinationTopology = CoordinationTopology.CENTRALIZED,
    structure: TaskStructure = TaskStructure.PARALLEL,
) -> DecompositionResult:
    """Build a DecompositionResult with created tasks from subtask defs."""
    plan = DecompositionPlan(
        parent_task_id=parent_task_id,
        subtasks=subtasks,
        task_structure=structure,
        coordination_topology=topology,
    )
    created_tasks = tuple(
        make_assignment_task(
            id=s.id,
            title=s.title,
            description=s.description,
            parent_task_id=parent_task_id,
            dependencies=s.dependencies,
        )
        for s in subtasks
    )
    edges: list[tuple[str, str]] = []
    for s in subtasks:
        edges.extend((dep, s.id) for dep in s.dependencies)
    return DecompositionResult(
        plan=plan,
        created_tasks=created_tasks,
        dependency_edges=tuple(edges),
    )


def make_routing(
    subtask_agent_pairs: list[tuple[str, str]],
    *,
    parent_task_id: str = "parent-1",
    topology: CoordinationTopology = CoordinationTopology.CENTRALIZED,
    unroutable: tuple[str, ...] = (),
) -> RoutingResult:
    """Build a RoutingResult from subtask-agent pairs."""
    decisions: list[RoutingDecision] = []
    for subtask_id, agent_name in subtask_agent_pairs:
        agent = make_assignment_agent(agent_name)
        decisions.append(
            RoutingDecision(
                subtask_id=subtask_id,
                selected_candidate=RoutingCandidate(
                    agent_identity=agent,
                    score=0.9,
                    reason="Good match",
                ),
                topology=topology,
            )
        )
    return RoutingResult(
        parent_task_id=parent_task_id,
        decisions=tuple(decisions),
        unroutable=unroutable,
    )


def build_run_result(task_id: str, agent_id: str) -> AgentRunResult:
    """Build a minimal AgentRunResult for testing."""
    from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
    from synthorg.engine.prompt import SystemPrompt

    identity = make_assignment_agent("test-agent")
    task = make_assignment_task(
        id=task_id,
        assigned_to=agent_id,
        status=TaskStatus.ASSIGNED,
    )
    ctx = AgentContext.from_identity(identity, task=task)
    execution_result = ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
    )
    return AgentRunResult(
        execution_result=execution_result,
        system_prompt=SystemPrompt(
            content="test",
            template_version="1.0",
            estimated_tokens=1,
            sections=("identity",),
            metadata={"agent_id": agent_id},
        ),
        duration_seconds=0.5,
        agent_id=agent_id,
        task_id=task_id,
    )


def make_exec_result(
    group_id: str,
    task_agent_pairs: list[tuple[str, str]],
    *,
    all_succeed: bool = True,
) -> ParallelExecutionResult:
    """Build a ParallelExecutionResult with given outcomes."""
    outcomes: list[AgentOutcome] = []
    for task_id, agent_id in task_agent_pairs:
        if all_succeed:
            run_result = build_run_result(task_id, agent_id)
            outcomes.append(
                AgentOutcome(
                    task_id=task_id,
                    agent_id=agent_id,
                    result=run_result,
                )
            )
        else:
            outcomes.append(
                AgentOutcome(
                    task_id=task_id,
                    agent_id=agent_id,
                    error="Test failure",
                )
            )

    return ParallelExecutionResult(
        group_id=group_id,
        outcomes=tuple(outcomes),
        total_duration_seconds=1.0,
    )
