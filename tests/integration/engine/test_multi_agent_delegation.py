"""Integration test: multi-agent delegation, decomposition, routing, execution.

Validates the full multi-agent pipeline end-to-end:
1. Delegation (CEO → Lead → workers)
2. Decomposition (ManualDecompositionStrategy)
3. Routing (AgentTaskScorer → TopologySelector → TaskRoutingService)
4. Parallel execution (ParallelExecutor with mock provider)
5. Status rollup
"""

from datetime import date
from typing import TYPE_CHECKING

import pytest

from synthorg.communication.config import (
    CircuitBreakerConfig,
    HierarchyConfig,
    LoopPreventionConfig,
    RateLimitConfig,
)
from synthorg.communication.delegation.authority import AuthorityValidator
from synthorg.communication.delegation.hierarchy import HierarchyResolver
from synthorg.communication.delegation.models import DelegationRequest
from synthorg.communication.delegation.service import DelegationService
from synthorg.communication.loop_prevention.guard import DelegationGuard
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.company import (
    Company,
    CompanyConfig,
    Department,
    Team,
)
from synthorg.core.enums import (
    Complexity,
    SeniorityLevel,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from synthorg.core.role import Authority
from synthorg.core.task import Task
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.assignment.models import (
    AgentWorkload,
    AssignmentRequest,
)
from synthorg.engine.assignment.service import TaskAssignmentService
from synthorg.engine.assignment.strategies import (
    LoadBalancedAssignmentStrategy,
    RoleBasedAssignmentStrategy,
)
from synthorg.engine.decomposition.classifier import TaskStructureClassifier
from synthorg.engine.decomposition.manual import ManualDecompositionStrategy
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    SubtaskDefinition,
)
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.parallel import ParallelExecutor
from synthorg.engine.parallel_models import (
    AgentAssignment,
    ParallelExecutionGroup,
    ParallelProgress,
)
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.routing.service import TaskRoutingService
from synthorg.engine.routing.topology_selector import TopologySelector
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from synthorg.providers.capabilities import ModelCapabilities

pytestmark = [pytest.mark.integration, pytest.mark.timeout(30)]


# ── Mock Provider ──────────────────────────────────────────────────


class _DeterministicProvider:
    """Mock provider that returns canned responses per task_id.

    Identifies the task by searching messages for task_id patterns.
    For ``fail_for`` task_ids, raises ``RuntimeError`` to simulate
    provider/execution failure.
    """

    def __init__(
        self,
        responses: dict[str, CompletionResponse],
        *,
        fail_for: frozenset[str] = frozenset(),
    ) -> None:
        self._responses = responses
        self._fail_for = fail_for

    def _extract_task_id(
        self,
        messages: list[ChatMessage],
    ) -> str | None:
        """Extract task_id from messages by searching content."""
        for msg in reversed(messages):
            if msg.content is None:
                continue
            for task_id in self._responses:
                if task_id in msg.content:
                    return task_id
            for task_id in self._fail_for:
                if task_id in msg.content:
                    return task_id
        return None

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Return canned response or raise for fail_for tasks."""
        task_id = self._extract_task_id(messages)

        if task_id is not None and task_id in self._fail_for:
            msg = f"Simulated failure for task {task_id}"
            raise RuntimeError(msg)

        if task_id is not None and task_id in self._responses:
            return self._responses[task_id]

        # Fallback: return a generic completion
        return CompletionResponse(
            content="Task completed successfully.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                input_tokens=50,
                output_tokens=20,
                cost_usd=0.005,
            ),
            model=model,
        )

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Not implemented for this test."""
        msg = "stream not supported"
        raise NotImplementedError(msg)

    async def get_model_capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        """Return minimal capabilities."""
        from synthorg.providers.capabilities import ModelCapabilities

        return ModelCapabilities(
            model_id=model,
            provider="test-provider",
            supports_tools=False,
            supports_streaming=False,
            max_context_tokens=8192,
            max_output_tokens=4096,
            cost_per_1k_input=0.01,
            cost_per_1k_output=0.03,
        )


# ── Agent Factories ────────────────────────────────────────────────


def _model_config() -> ModelConfig:
    return ModelConfig(
        provider="test-provider",
        model_id="test-small-001",
    )


def _make_agent(  # noqa: PLR0913
    name: str,
    role: str,
    *,
    level: SeniorityLevel = SeniorityLevel.MID,
    primary_skills: tuple[str, ...] = (),
    secondary_skills: tuple[str, ...] = (),
    can_delegate_to: tuple[str, ...] = (),
) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role=role,
        department="Engineering",
        level=level,
        model=_model_config(),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(
            primary=primary_skills,
            secondary=secondary_skills,
        ),
        authority=Authority(can_delegate_to=can_delegate_to),
    )


def _make_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": "task-root",
        "title": "Build feature",
        "description": "Build the full feature end to end",
        "type": TaskType.DEVELOPMENT,
        "project": "proj-main",
        "created_by": "ceo",
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


# ── 4-Agent Hierarchy ──────────────────────────────────────────────


def _build_agent_pool() -> dict[str, AgentIdentity]:
    """Build a 4-agent hierarchy: CEO → Lead → Backend + Frontend."""
    return {
        "ceo": _make_agent(
            "ceo",
            "CEO",
            level=SeniorityLevel.VP,
            primary_skills=("strategy",),
        ),
        "lead": _make_agent(
            "lead",
            "Lead Developer",
            level=SeniorityLevel.LEAD,
            primary_skills=("architecture", "python"),
        ),
        "backend": _make_agent(
            "backend",
            "Backend Developer",
            level=SeniorityLevel.MID,
            primary_skills=("python", "api-design"),
            secondary_skills=("databases",),
        ),
        "frontend": _make_agent(
            "frontend",
            "Frontend Developer",
            level=SeniorityLevel.MID,
            primary_skills=("typescript", "react"),
            secondary_skills=("css",),
        ),
    }


def _build_pipeline(
    agents: dict[str, AgentIdentity],
) -> tuple[
    DelegationService,
    DecompositionService,
    TaskRoutingService,
]:
    """Build the full delegation → decomposition → routing pipeline.

    Args:
        agents: Agent pool keyed by name.

    Returns:
        Tuple of services.
    """
    # Company structure
    company = Company(
        name="Test Corp",
        departments=(
            Department(
                name="Engineering",
                head="ceo",
                budget_percent=100.0,
                teams=(
                    Team(
                        name="core",
                        lead="lead",
                        members=("backend", "frontend"),
                    ),
                ),
            ),
        ),
        config=CompanyConfig(budget_monthly=1000.0),
    )

    # Delegation service
    hierarchy = HierarchyResolver(company)
    hierarchy_config = HierarchyConfig(
        enforce_chain_of_command=True,
        allow_skip_level=True,
    )
    authority_validator = AuthorityValidator(
        hierarchy,
        hierarchy_config,
    )
    guard = DelegationGuard(
        LoopPreventionConfig(
            max_delegation_depth=5,
            rate_limit=RateLimitConfig(
                max_per_pair_per_minute=10,
                burst_allowance=3,
            ),
            circuit_breaker=CircuitBreakerConfig(
                bounce_threshold=5,
                cooldown_seconds=300,
            ),
        ),
    )
    delegation_service = DelegationService(
        hierarchy=hierarchy,
        authority_validator=authority_validator,
        guard=guard,
    )

    # Decomposition service (plan set per-test)
    decomposition_service = DecompositionService(
        strategy=ManualDecompositionStrategy(
            DecompositionPlan(
                parent_task_id="placeholder",
                subtasks=(
                    SubtaskDefinition(
                        id="placeholder-sub",
                        title="Placeholder",
                        description="Replaced per-test",
                    ),
                ),
            ),
        ),
        classifier=TaskStructureClassifier(),
    )

    # Routing service
    scorer = AgentTaskScorer()
    topology_selector = TopologySelector()
    routing_service = TaskRoutingService(
        scorer=scorer,
        topology_selector=topology_selector,
    )

    return (
        delegation_service,
        decomposition_service,
        routing_service,
    )


def _make_decomposition_service(
    parent_task_id: str,
    subtasks: tuple[SubtaskDefinition, ...],
) -> DecompositionService:
    """Build a DecompositionService with a specific plan."""
    plan = DecompositionPlan(
        parent_task_id=parent_task_id,
        subtasks=subtasks,
        task_structure=TaskStructure.PARALLEL,
    )
    return DecompositionService(
        strategy=ManualDecompositionStrategy(plan),
        classifier=TaskStructureClassifier(),
    )


def _make_response(
    content: str,
    *,
    cost: float = 0.005,
) -> CompletionResponse:
    """Build a simple completion response."""
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(
            input_tokens=50,
            output_tokens=20,
            cost_usd=cost,
        ),
        model="test-small-001",
    )


# ── Test Scenarios ─────────────────────────────────────────────────


class TestHappyPathDecomposeRouteExecute:
    """Scenario 1: delegate → decompose → route → execute."""

    async def test_happy_path(self) -> None:
        """Full pipeline succeeds with 2 subtasks to 2 agents."""
        agents = _build_agent_pool()
        delegation_svc, _, routing_svc = _build_pipeline(agents)

        # 1. Create root task
        root = _make_task()
        assert root.status == TaskStatus.CREATED

        # 2. CEO delegates to Lead
        req = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="lead",
            task=root,
            refinement="Focus on API and UI components",
        )
        dr = delegation_svc.delegate(
            req,
            agents["ceo"],
            agents["lead"],
        )
        assert dr.success is True
        delegated_task = dr.delegated_task
        assert delegated_task is not None
        assert delegated_task.delegation_chain == ("ceo",)

        # 3. Lead decomposes into 2 subtasks
        subtasks = (
            SubtaskDefinition(
                id="subtask-api",
                title="Build REST API",
                description="[subtask-api] Implement API endpoints",
                estimated_complexity=Complexity.MEDIUM,
                required_skills=("python", "api-design"),
                required_role="Backend Developer",
            ),
            SubtaskDefinition(
                id="subtask-ui",
                title="Build UI components",
                description="[subtask-ui] Implement React components",
                estimated_complexity=Complexity.MEDIUM,
                required_skills=("typescript", "react"),
                required_role="Frontend Developer",
            ),
        )
        decomp_svc = _make_decomposition_service(
            delegated_task.id,
            subtasks,
        )
        decomp_result = await decomp_svc.decompose_task(
            delegated_task,
            DecompositionContext(),
        )
        assert len(decomp_result.created_tasks) == 2

        # 4. Route subtasks
        workers = (agents["backend"], agents["frontend"])
        routing_result = routing_svc.route(
            decomp_result,
            workers,
            delegated_task,
        )
        assert len(routing_result.decisions) == 2
        assert len(routing_result.unroutable) == 0

        # Verify routing: api → backend, ui → frontend
        api_decision = next(
            d for d in routing_result.decisions if d.subtask_id == "subtask-api"
        )
        ui_decision = next(
            d for d in routing_result.decisions if d.subtask_id == "subtask-ui"
        )
        assert api_decision.selected_candidate.agent_identity.name == "backend"
        assert ui_decision.selected_candidate.agent_identity.name == "frontend"

        # 5. Transition subtasks to ASSIGNED
        api_task = next(t for t in decomp_result.created_tasks if t.id == "subtask-api")
        ui_task = next(t for t in decomp_result.created_tasks if t.id == "subtask-ui")
        api_assigned = api_task.with_transition(
            TaskStatus.ASSIGNED,
            assigned_to=str(agents["backend"].id),
        )
        ui_assigned = ui_task.with_transition(
            TaskStatus.ASSIGNED,
            assigned_to=str(agents["frontend"].id),
        )

        # 6. Execute via ParallelExecutor
        provider = _DeterministicProvider(
            responses={
                "subtask-api": _make_response(
                    "API endpoints implemented.",
                ),
                "subtask-ui": _make_response(
                    "UI components built.",
                ),
            },
        )
        engine = AgentEngine(provider=provider)
        executor = ParallelExecutor(engine=engine)

        group = ParallelExecutionGroup(
            group_id="happy-path-group",
            assignments=(
                AgentAssignment(
                    identity=agents["backend"],
                    task=api_assigned,
                    max_turns=3,
                ),
                AgentAssignment(
                    identity=agents["frontend"],
                    task=ui_assigned,
                    max_turns=3,
                ),
            ),
        )

        exec_result = await executor.execute_group(group)

        assert exec_result.all_succeeded is True
        assert exec_result.agents_succeeded == 2
        assert exec_result.total_cost_usd > 0

        # 7. Rollup status
        subtask_statuses = tuple(
            TaskStatus.COMPLETED if o.is_success else TaskStatus.FAILED
            for o in exec_result.outcomes
        )
        rollup = decomp_svc.rollup_status(
            delegated_task.id,
            subtask_statuses,
        )
        assert rollup.derived_parent_status == TaskStatus.COMPLETED
        assert rollup.completed == 2
        assert rollup.failed == 0

        # Verify audit trail
        trail = delegation_svc.get_audit_trail()
        assert len(trail) == 1


class TestPartialFailure:
    """Scenario 2: One subtask fails, other succeeds."""

    async def test_partial_failure(self) -> None:
        """Backend fails, frontend succeeds — rollup is FAILED."""
        agents = _build_agent_pool()
        delegation_svc, _, _ = _build_pipeline(agents)

        root = _make_task()

        # Delegate
        req = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="lead",
            task=root,
        )
        dr = delegation_svc.delegate(
            req,
            agents["ceo"],
            agents["lead"],
        )
        assert dr.success is True
        delegated_task = dr.delegated_task
        assert delegated_task is not None

        # Decompose — embed task IDs in descriptions so
        # the mock provider can identify which task is executing
        subtasks = (
            SubtaskDefinition(
                id="subtask-api-fail",
                title="Build REST API",
                description="[subtask-api-fail] Implement API endpoints",
                estimated_complexity=Complexity.MEDIUM,
                required_skills=("python",),
            ),
            SubtaskDefinition(
                id="subtask-ui-ok",
                title="Build UI",
                description="[subtask-ui-ok] Implement React components",
                estimated_complexity=Complexity.MEDIUM,
                required_skills=("typescript",),
            ),
        )
        decomp_svc = _make_decomposition_service(
            delegated_task.id,
            subtasks,
        )
        decomp_result = await decomp_svc.decompose_task(
            delegated_task,
            DecompositionContext(),
        )

        # Assign
        api_task = next(
            t for t in decomp_result.created_tasks if t.id == "subtask-api-fail"
        )
        ui_task = next(
            t for t in decomp_result.created_tasks if t.id == "subtask-ui-ok"
        )
        api_assigned = api_task.with_transition(
            TaskStatus.ASSIGNED,
            assigned_to=str(agents["backend"].id),
        )
        ui_assigned = ui_task.with_transition(
            TaskStatus.ASSIGNED,
            assigned_to=str(agents["frontend"].id),
        )

        # Execute with backend failing
        provider = _DeterministicProvider(
            responses={
                "subtask-ui-ok": _make_response("UI built."),
            },
            fail_for=frozenset({"subtask-api-fail"}),
        )
        engine = AgentEngine(provider=provider)
        executor = ParallelExecutor(engine=engine)

        group = ParallelExecutionGroup(
            group_id="partial-failure-group",
            assignments=(
                AgentAssignment(
                    identity=agents["backend"],
                    task=api_assigned,
                    max_turns=3,
                ),
                AgentAssignment(
                    identity=agents["frontend"],
                    task=ui_assigned,
                    max_turns=3,
                ),
            ),
        )

        exec_result = await executor.execute_group(group)

        # One succeeded, one failed
        assert exec_result.all_succeeded is False
        assert exec_result.agents_succeeded == 1
        assert exec_result.agents_failed == 1

        # Frontend succeeded
        frontend_outcome = next(
            o for o in exec_result.outcomes if o.task_id == "subtask-ui-ok"
        )
        assert frontend_outcome.is_success is True

        # Backend failed
        backend_outcome = next(
            o for o in exec_result.outcomes if o.task_id == "subtask-api-fail"
        )
        assert backend_outcome.is_success is False

        # Rollup: 1 completed + 1 failed = FAILED
        subtask_statuses = tuple(
            TaskStatus.COMPLETED if o.is_success else TaskStatus.FAILED
            for o in exec_result.outcomes
        )
        rollup = decomp_svc.rollup_status(
            delegated_task.id,
            subtask_statuses,
        )
        assert rollup.derived_parent_status == TaskStatus.FAILED
        assert rollup.completed == 1
        assert rollup.failed == 1


class TestLoopPrevention:
    """Scenario 3: Delegation back to ancestors is blocked."""

    def test_loop_prevention_blocks_back_delegation(self) -> None:
        """Back-delegation to ancestors is blocked by the guard."""
        agents = _build_agent_pool()
        delegation_svc, _, _ = _build_pipeline(agents)

        root = _make_task()

        # 1. CEO → Lead succeeds
        req1 = DelegationRequest(
            delegator_id="ceo",
            delegatee_id="lead",
            task=root,
        )
        r1 = delegation_svc.delegate(
            req1,
            agents["ceo"],
            agents["lead"],
        )
        assert r1.success is True
        sub1 = r1.delegated_task
        assert sub1 is not None
        assert sub1.delegation_chain == ("ceo",)

        # 2. Lead → Backend succeeds
        sub1_for_delegation = Task(
            id=sub1.id,
            title="Implement backend",
            description=sub1.description,
            type=sub1.type,
            project=sub1.project,
            created_by=sub1.created_by,
            parent_task_id=sub1.parent_task_id,
            delegation_chain=sub1.delegation_chain,
        )
        req2 = DelegationRequest(
            delegator_id="lead",
            delegatee_id="backend",
            task=sub1_for_delegation,
        )
        r2 = delegation_svc.delegate(
            req2,
            agents["lead"],
            agents["backend"],
        )
        assert r2.success is True
        sub2 = r2.delegated_task
        assert sub2 is not None
        assert sub2.delegation_chain == ("ceo", "lead")

        # 3. Backend → CEO: blocked (CEO in chain)
        req3 = DelegationRequest(
            delegator_id="backend",
            delegatee_id="ceo",
            task=sub2,
        )
        r3 = delegation_svc.delegate(
            req3,
            agents["backend"],
            agents["ceo"],
        )
        assert r3.success is False
        assert r3.blocked_by is not None

        # 4. Backend → Lead: blocked (Lead in chain)
        req4 = DelegationRequest(
            delegator_id="backend",
            delegatee_id="lead",
            task=sub2,
        )
        r4 = delegation_svc.delegate(
            req4,
            agents["backend"],
            agents["lead"],
        )
        assert r4.success is False
        assert r4.blocked_by is not None

        # Only 2 successful delegations in audit trail
        trail = delegation_svc.get_audit_trail()
        assert len(trail) == 2


class TestParallelExecutionConcurrency:
    """Scenario 4: Parallel execution with concurrency limit."""

    async def test_concurrency_limit(self) -> None:
        """3 agents, 3 subtasks, max_concurrency=2 — all succeed."""
        agents = _build_agent_pool()

        # Add QA agent as 3rd worker
        qa_agent = _make_agent(
            "qa",
            "QA Engineer",
            level=SeniorityLevel.MID,
            primary_skills=("testing", "python"),
        )

        # Create 3 subtasks
        agent_map = {
            "api": agents["backend"],
            "ui": agents["frontend"],
            "test": qa_agent,
        }
        tasks: list[Task] = []
        for suffix, agent in agent_map.items():
            t = _make_task(
                id=f"subtask-{suffix}",
                title=f"Work on {suffix}",
                description=f"[subtask-{suffix}] Execute {suffix} work",
                assigned_to=str(agent.id),
                status="assigned",
            )
            tasks.append(t)

        provider = _DeterministicProvider(
            responses={
                "subtask-api": _make_response(
                    "API done.",
                    cost=0.003,
                ),
                "subtask-ui": _make_response(
                    "UI done.",
                    cost=0.004,
                ),
                "subtask-test": _make_response(
                    "Tests done.",
                    cost=0.002,
                ),
            },
        )
        engine = AgentEngine(provider=provider)

        # Track progress snapshots
        progress_snapshots: list[ParallelProgress] = []

        def on_progress(p: ParallelProgress) -> None:
            progress_snapshots.append(p)

        executor = ParallelExecutor(
            engine=engine,
            progress_callback=on_progress,
        )

        group = ParallelExecutionGroup(
            group_id="concurrency-group",
            assignments=(
                AgentAssignment(
                    identity=agents["backend"],
                    task=tasks[0],
                    max_turns=3,
                ),
                AgentAssignment(
                    identity=agents["frontend"],
                    task=tasks[1],
                    max_turns=3,
                ),
                AgentAssignment(
                    identity=qa_agent,
                    task=tasks[2],
                    max_turns=3,
                ),
            ),
            max_concurrency=2,
        )

        exec_result = await executor.execute_group(group)

        # All 3 succeed
        assert exec_result.all_succeeded is True
        assert exec_result.agents_succeeded == 3
        assert exec_result.total_cost_usd > 0

        # Progress callback was invoked
        assert len(progress_snapshots) > 0

        # in_progress never exceeds max_concurrency=2
        for snapshot in progress_snapshots:
            assert snapshot.in_progress <= 2


class TestTaskAssignmentServiceIntegration:
    """Integration test: TaskAssignmentService with scoring pipeline."""

    def test_assignment_service_selects_best_agent(self) -> None:
        """TaskAssignmentService integrates with AgentTaskScorer."""
        agents = _build_agent_pool()
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)
        service = TaskAssignmentService(strategy)

        task = _make_task()
        request = AssignmentRequest(
            task=task,
            available_agents=(
                agents["backend"],
                agents["frontend"],
            ),
            required_skills=("python", "api-design"),
            required_role="Backend Developer",
        )

        result = service.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "backend"
        assert result.strategy_used == "role_based"

    def test_load_balanced_prefers_least_loaded(self) -> None:
        """LoadBalancedAssignmentStrategy picks the least-loaded agent."""
        agents = _build_agent_pool()
        scorer = AgentTaskScorer()
        strategy = LoadBalancedAssignmentStrategy(scorer)
        service = TaskAssignmentService(strategy)

        task = _make_task(
            required_skills=("python",),
        )

        # Both agents match python; backend has higher workload
        request = AssignmentRequest(
            task=task,
            available_agents=(
                agents["backend"],
                agents["lead"],
            ),
            required_skills=("python",),
            workloads=(
                AgentWorkload(
                    agent_id=str(agents["backend"].id),
                    active_task_count=5,
                ),
                AgentWorkload(
                    agent_id=str(agents["lead"].id),
                    active_task_count=1,
                ),
            ),
        )

        result = service.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "lead"
        assert result.strategy_used == "load_balanced"
        assert len(result.alternatives) >= 1
