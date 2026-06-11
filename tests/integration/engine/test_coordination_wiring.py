"""Integration test: coordinator runtime wiring.

Validates the bootstrap-to-API wiring path:
1. Create RootConfig with coordination section
2. Create a mock coordinator (real build_coordinator() is unit-tested separately)
3. Create TaskEngine with mock persistence
4. Create AgentRegistryService and register test agents
5. Build app via create_app() with coordinator + agent_registry
6. Use LoopAsyncClient to create a task and trigger coordination
"""

from collections.abc import AsyncIterator
from datetime import date
from uuid import uuid4

import pytest

from synthorg.api.auth.service import AuthService
from synthorg.budget.tracker import CostTracker
from synthorg.config.schema import RootConfig
from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.auth.config import AuthConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.core.role import Authority, Skill
from synthorg.core.task_enums import CoordinationTopology
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.hr.enums import AgentStatus
from synthorg.hr.registry import AgentRegistryService
from synthorg.hr.seniority import SeniorityLevel
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolDefinition,
)
from tests._shared import LoopAsyncClient, as_uuid
from tests._shared import build_test_app as create_app
from tests.unit.api.conftest import (
    FakeMessageBus,
    FakePersistenceBackend,
    make_auth_headers,
    make_task,
)
from tests.unit.engine.task_engine_helpers import (
    FakeMessageBus as EngineMessageBus,
)
from tests.unit.engine.task_engine_helpers import (
    FakePersistence,
)

pytestmark = pytest.mark.integration
_TEST_JWT_SECRET = "test-secret-that-is-at-least-32-characters-long"

# ── Mock Provider ──────────────────────────────────────────────────


class _DeterministicProvider:
    """Mock provider that returns a fixed completion."""

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        return CompletionResponse(
            content="Task completed successfully.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                cost=0.001,
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
        msg = "stream not supported"
        raise NotImplementedError(msg)

    async def get_model_capabilities(
        self,
        model: str,
    ) -> None:
        return None


# ── Helpers ────────────────────────────────────────────────────────


def _make_test_agent(
    name: str,
    *,
    skills: tuple[str, ...] = ("python", "testing"),
) -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name=name,
        role="developer",
        department="engineering",
        level=SeniorityLevel.MID,
        skills=SkillSet(primary=tuple(Skill(id=s, name=s) for s in skills)),
        authority=Authority(budget_limit=10.0),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        status=AgentStatus.ACTIVE,
    )


def _seed_test_users(
    backend: FakePersistenceBackend,
    auth_service: AuthService,
) -> None:
    """Pre-seed users for auth (delegates to conftest helper)."""
    from tests.unit.api.conftest import (
        _seed_test_users as conftest_seed,
    )

    conftest_seed(backend, auth_service)


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.integration
class TestBuildCoordinatorFactory:
    """Verify build_coordinator() wires real services from config."""

    def test_build_coordinator_with_defaults(self) -> None:
        """Factory produces a coordinator from default config."""
        from unittest.mock import AsyncMock

        from synthorg.config.agent_schema import TaskAssignmentConfig
        from synthorg.engine.coordination.factory import build_coordinator
        from synthorg.engine.coordination.section_config import (
            CoordinationSectionConfig,
        )
        from synthorg.engine.coordination.service import (
            MultiAgentCoordinator,
        )

        config = CoordinationSectionConfig()
        engine = AsyncMock()
        task_assignment_config = TaskAssignmentConfig()

        coordinator = build_coordinator(
            config=config,
            engine=engine,
            task_assignment_config=task_assignment_config,
        )

        assert isinstance(coordinator, MultiAgentCoordinator)

    async def test_build_coordinator_no_provider_decomposition_raises(
        self,
    ) -> None:
        """Coordinator built without provider raises on decomposition."""
        from unittest.mock import AsyncMock

        from synthorg.config.agent_schema import TaskAssignmentConfig
        from synthorg.engine.coordination.factory import build_coordinator
        from synthorg.engine.coordination.section_config import (
            CoordinationSectionConfig,
        )
        from synthorg.engine.errors import DecompositionError

        config = CoordinationSectionConfig()
        engine = AsyncMock()
        task_assignment_config = TaskAssignmentConfig()

        coordinator = build_coordinator(
            config=config,
            engine=engine,
            task_assignment_config=task_assignment_config,
        )

        # Decomposition should fail since no provider was given
        from synthorg.core.task import Task
        from synthorg.core.task_enums import Priority, TaskType
        from synthorg.engine.decomposition.models import (
            DecompositionContext,
        )

        task = Task(
            id=as_uuid("test-task-001"),
            title="test",
            description="test task",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="test",
            created_by="test-agent",
        )
        context = DecompositionContext(max_subtasks=5)

        with pytest.raises(DecompositionError, match="No LLM provider"):
            await coordinator._decomposition_service.decompose_task(task, context)


@pytest.mark.integration
class TestCoordinationWiring:
    """Full bootstrap → API → coordination integration test."""

    async def test_build_coordinator_and_coordinate_via_api(self) -> None:
        """End-to-end: build coordinator, create app, coordinate task."""
        # 1. Create config
        config = RootConfig(company_name="test-corp")

        # 2. Build a coordinator that wraps real services but uses
        # a task-id-aware manual strategy so decomposition succeeds
        from unittest.mock import AsyncMock

        from synthorg.engine.coordination.attribution import (
            CoordinationResultWithAttribution,
        )
        from synthorg.engine.coordination.models import (
            CoordinationPhaseResult,
            CoordinationResult,
        )

        async def _mock_coordinate(context):  # type: ignore[no-untyped-def]
            """Return a realistic result keyed to the actual task."""
            result = CoordinationResult(
                parent_task_id=str(context.task.id),
                topology=CoordinationTopology.SAS,
                phases=(
                    CoordinationPhaseResult(
                        phase="decompose",
                        success=True,
                        duration_seconds=0.01,
                    ),
                    CoordinationPhaseResult(
                        phase="route",
                        success=True,
                        duration_seconds=0.01,
                    ),
                ),
                total_duration_seconds=0.05,
                total_cost=0.001,
            )
            return CoordinationResultWithAttribution(result=result)

        coordinator = AsyncMock()
        coordinator.coordinate.side_effect = _mock_coordinate

        # 3. Create task engine
        engine_persistence = FakePersistence()
        task_engine = TaskEngine(
            config=TaskEngineConfig(),
            persistence=engine_persistence,  # type: ignore[arg-type]
            message_bus=EngineMessageBus(),  # type: ignore[arg-type]
        )

        # 4. Create agent registry and register agents
        registry = AgentRegistryService()
        agent_alice = _make_test_agent("alice", skills=("python", "testing"))
        await registry.register(agent_alice)

        # 5. Build app
        import synthorg.settings.definitions  # noqa: F401
        from synthorg.settings.registry import get_registry
        from synthorg.settings.service import SettingsService

        backend = FakePersistenceBackend()
        auth_service = AuthService(AuthConfig(jwt_secret=_TEST_JWT_SECRET))
        _seed_test_users(backend, auth_service)

        settings_service = SettingsService(
            repository=backend.settings,
            registry=get_registry(),
        )

        # A scripted provider so the company is not empty-company:
        # task creation is rejected (409) without an active provider.
        from synthorg.config.provider_schema import ProviderConfig
        from synthorg.providers.registry import ProviderRegistry

        app = create_app(
            config=config,
            persistence=backend,
            message_bus=FakeMessageBus(),
            cost_tracker=CostTracker(),
            auth_service=auth_service,
            task_engine=task_engine,
            coordinator=coordinator,
            agent_registry=registry,
            settings_service=settings_service,
            provider_registry=ProviderRegistry.from_config(
                {"test-provider": ProviderConfig(driver="scripted")},
            ),
        )

        # 6. Use LoopAsyncClient
        async with LoopAsyncClient(app) as client:
            client.headers.update(make_auth_headers("ceo"))

            # ``POST /tasks`` is a 202 board handoff (no task id in the
            # response); task materialisation runs asynchronously in the
            # pipeline spine. Seed the task directly into the engine's
            # in-memory repository so the coordinate call below has a
            # concrete task id to operate on (same pattern as
            # ``tests/unit/api/controllers/test_coordination.py::_insert_task``).
            seeded = make_task(
                task_id="integration-coordination-task",
                title="Integration test task",
                project="test-project",
                created_by="api",
            )
            task_id = str(seeded.id)
            engine_persistence.tasks._tasks[task_id] = seeded

            # Coordinate
            resp = await client.post(
                f"/api/v1/tasks/{task_id}/coordinate",
                json={"agent_names": ["alice"]},
            )
            assert resp.status_code == 200, resp.json()
            body = resp.json()
            assert body["success"] is True
            data = body["data"]
            assert data["parent_task_id"] == task_id
            assert data["topology"] == "sas"
            assert isinstance(data["total_duration_seconds"], float)
            assert isinstance(data["phases"], list)
            assert len(data["phases"]) >= 1
