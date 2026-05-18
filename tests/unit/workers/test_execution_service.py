"""Unit tests for the agent-runtime worker execution services."""

from unittest.mock import AsyncMock

import pytest
from structlog.testing import capture_logs

from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    ConflictError,
    NotFoundError,
)
from synthorg.core.enums import TaskStatus
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.observability.events.workers import (
    WORKERS_EXECUTION_SERVICE_FAILED,
)
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.models import AutonomyConfig
from synthorg.security.autonomy.resolver import AutonomyResolver
from synthorg.tools.sandbox.lifecycle.config import (
    STRATEGY_PER_AGENT,
    STRATEGY_PER_CALL,
    STRATEGY_PER_TASK,
)
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
    LifecycleAdvancingExecutionService,
    NoProviderExecutionService,
)
from tests._shared import mock_of
from tests._shared.scripted_provider import make_e2e_identity, make_e2e_task

pytestmark = pytest.mark.unit


def _run_result() -> object:
    return mock_of[AgentRunResult](
        termination_reason=TerminationReason.COMPLETED,
        total_turns=1,
    )


class TestNoProviderExecutionService:
    async def test_execute_once_rejects(self) -> None:
        service = NoProviderExecutionService()
        with pytest.raises(AgentRuntimeNotConfiguredError, match="empty mode"):
            await service.execute_once(
                task_id="task-1",
                previous_status=None,
                new_status="assigned",
                idempotency_key="k",
                requested_by="user",
            )


class TestAgentEngineExecutionService:
    async def _make_service(
        self,
        *,
        task_engine: TaskEngine,
        agent_registry: AgentRegistryService,
        engine: AgentEngine,
    ) -> AgentEngineExecutionService:
        return AgentEngineExecutionService(
            engine=engine,
            task_engine=task_engine,
            agent_registry=agent_registry,
            autonomy_resolver=AutonomyResolver(
                registry=ActionTypeRegistry(),
                config=AutonomyConfig(),
            ),
        )

    async def test_task_not_found_raises(self) -> None:
        task_engine = mock_of[TaskEngine](get_task=AsyncMock(return_value=None))
        service = await self._make_service(
            task_engine=task_engine,
            agent_registry=AgentRegistryService(),
            engine=mock_of[AgentEngine](run=AsyncMock()),
        )
        with pytest.raises(NotFoundError):
            await service.execute_once(
                task_id="missing",
                previous_status=None,
                new_status="assigned",
                idempotency_key="k",
                requested_by="user",
            )

    async def test_unregistered_agent_raises(self) -> None:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        task_engine = mock_of[TaskEngine](get_task=AsyncMock(return_value=task))
        service = await self._make_service(
            task_engine=task_engine,
            agent_registry=AgentRegistryService(),  # nothing registered
            engine=mock_of[AgentEngine](run=AsyncMock()),
        )
        with pytest.raises(AgentRuntimeNotConfiguredError, match="not"):
            await service.execute_once(
                task_id=task.id,
                previous_status=None,
                new_status="assigned",
                idempotency_key="k",
                requested_by="user",
            )

    async def test_unassigned_task_raises_conflict(self) -> None:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity).model_copy(
            update={"assigned_to": None},
        )
        task_engine = mock_of[TaskEngine](get_task=AsyncMock(return_value=task))
        service = await self._make_service(
            task_engine=task_engine,
            agent_registry=AgentRegistryService(),
            engine=mock_of[AgentEngine](run=AsyncMock()),
        )
        with pytest.raises(ConflictError, match="not assigned"):
            await service.execute_once(
                task_id=task.id,
                previous_status=None,
                new_status="assigned",
                idempotency_key="k",
                requested_by="user",
            )

    async def test_engine_run_failure_logs_and_reraises(self) -> None:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        registry = AgentRegistryService()
        await registry.register(identity)
        engine_run = AsyncMock(side_effect=RuntimeError("provider boom"))
        task_engine = mock_of[TaskEngine](get_task=AsyncMock(return_value=task))
        service = await self._make_service(
            task_engine=task_engine,
            agent_registry=registry,
            engine=mock_of[AgentEngine](run=engine_run),
        )

        with capture_logs() as logs, pytest.raises(RuntimeError, match="boom"):
            await service.execute_once(
                task_id=task.id,
                previous_status="assigned",
                new_status="in_progress",
                idempotency_key="k",
                requested_by="user",
            )

        assert any(
            entry.get("log_level") == "error"
            and entry.get("event") == WORKERS_EXECUTION_SERVICE_FAILED
            and entry.get("task_id") == task.id
            and entry.get("agent_id") == str(identity.id)
            for entry in logs
        )

    async def test_happy_path_runs_engine_and_returns_post_state(self) -> None:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        post = task.model_copy(update={"status": TaskStatus.IN_REVIEW})
        registry = AgentRegistryService()
        await registry.register(identity)
        engine_run = AsyncMock(return_value=_run_result())
        task_engine = mock_of[TaskEngine](
            get_task=AsyncMock(side_effect=[task, post]),
        )
        service = await self._make_service(
            task_engine=task_engine,
            agent_registry=registry,
            engine=mock_of[AgentEngine](run=engine_run),
        )

        result = await service.execute_once(
            task_id=task.id,
            previous_status="assigned",
            new_status="in_progress",
            idempotency_key="k",
            requested_by="user",
        )

        assert result.status == TaskStatus.IN_REVIEW
        engine_run.assert_awaited_once()
        assert engine_run.await_args is not None
        kwargs = engine_run.await_args.kwargs
        assert kwargs["identity"] is identity
        assert kwargs["task"] is task
        # SUPERVISED default preset -> a real EffectiveAutonomy verdict.
        assert kwargs["effective_autonomy"] is not None

    async def test_autonomy_resolution_failure_degrades_to_none(self) -> None:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        post = task.model_copy(update={"status": TaskStatus.IN_REVIEW})
        registry = AgentRegistryService()
        await registry.register(identity)
        engine_run = AsyncMock(return_value=_run_result())
        service = AgentEngineExecutionService(
            engine=mock_of[AgentEngine](run=engine_run),
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(side_effect=[task, post]),
            ),
            agent_registry=registry,
            autonomy_resolver=mock_of[AutonomyResolver](
                resolve=lambda **_: (_ for _ in ()).throw(ValueError("bad seniority")),
            ),
        )

        await service.execute_once(
            task_id=task.id,
            previous_status=None,
            new_status="assigned",
            idempotency_key="k",
            requested_by="user",
        )

        assert engine_run.await_args is not None
        assert engine_run.await_args.kwargs["effective_autonomy"] is None

    async def test_identity_resolved_by_name_fallback(self) -> None:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity).model_copy(
            update={"assigned_to": identity.name},
        )
        post = task.model_copy(update={"status": TaskStatus.IN_REVIEW})
        registry = AgentRegistryService()
        await registry.register(identity)
        engine_run = AsyncMock(return_value=_run_result())
        service = await self._make_service(
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(side_effect=[task, post]),
            ),
            agent_registry=registry,
            engine=mock_of[AgentEngine](run=engine_run),
        )

        await service.execute_once(
            task_id=task.id,
            previous_status="assigned",
            new_status="in_progress",
            idempotency_key="k",
            requested_by="user",
        )

        assert engine_run.await_args is not None
        # Resolved via get_by_name (id lookup misses on a name string).
        assert engine_run.await_args.kwargs["identity"] is identity

    async def test_none_autonomy_resolver_passes_none(self) -> None:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        post = task.model_copy(update={"status": TaskStatus.IN_REVIEW})
        registry = AgentRegistryService()
        await registry.register(identity)
        engine_run = AsyncMock(return_value=_run_result())
        service = AgentEngineExecutionService(
            engine=mock_of[AgentEngine](run=engine_run),
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(side_effect=[task, post]),
            ),
            agent_registry=registry,
            autonomy_resolver=None,
        )

        await service.execute_once(
            task_id=task.id,
            previous_status=None,
            new_status="assigned",
            idempotency_key="k",
            requested_by="user",
        )

        assert engine_run.await_args is not None
        assert engine_run.await_args.kwargs["effective_autonomy"] is None

    async def test_task_missing_post_run_raises(self) -> None:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        registry = AgentRegistryService()
        await registry.register(identity)
        service = await self._make_service(
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(side_effect=[task, None]),
            ),
            agent_registry=registry,
            engine=mock_of[AgentEngine](run=AsyncMock(return_value=_run_result())),
        )

        with pytest.raises(NotFoundError, match="after execution"):
            await service.execute_once(
                task_id=task.id,
                previous_status="assigned",
                new_status="in_progress",
                idempotency_key="k",
                requested_by="user",
            )


class _StubGate:
    """Minimal ApprovalGate surface for dispatch_resume tests."""

    def __init__(self, resumed: object) -> None:
        from unittest.mock import MagicMock

        self.resume_context = AsyncMock(return_value=resumed)
        self.build_resume_message = MagicMock(
            return_value="[SYSTEM: APPROVED]",
        )


class _StubEngine:
    """Minimal AgentEngine surface for dispatch_resume tests."""

    def __init__(self, gate: object) -> None:
        self._approval_gate = gate
        self.resume_parked_run = AsyncMock(return_value=_run_result())


class TestDispatchResume:
    """dispatch_resume restores via the shared gate and re-runs."""

    def _service(self, engine: object) -> AgentEngineExecutionService:
        return AgentEngineExecutionService(
            engine=engine,  # type: ignore[arg-type]
            task_engine=mock_of[TaskEngine](),
            agent_registry=AgentRegistryService(),
            autonomy_resolver=AutonomyResolver(
                registry=ActionTypeRegistry(),
                config=AutonomyConfig(),
            ),
        )

    async def test_dispatch_resumes_via_shared_gate(self) -> None:
        from synthorg.engine.context import AgentContext

        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        ctx = AgentContext.from_identity(identity, task=task)
        gate = _StubGate(resumed=(ctx, "parked-1"))
        engine = _StubEngine(gate)
        service = self._service(engine)

        await service.dispatch_resume(
            approval_id="approval-1",
            approved=True,
            decided_by="admin",
            decision_reason="ship it",
        )
        await service.drain_resume_tasks()

        gate.resume_context.assert_awaited_once_with("approval-1")
        gate.build_resume_message.assert_called_once_with(
            "approval-1",
            approved=True,
            decided_by="admin",
            decision_reason="ship it",
        )
        engine.resume_parked_run.assert_awaited_once()
        call = engine.resume_parked_run.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["parked_context"] is ctx
        assert kwargs["approval_id"] == "approval-1"
        assert kwargs["decision_message"] == "[SYSTEM: APPROVED]"

    async def test_dispatch_no_parked_context_is_noop(self) -> None:
        gate = _StubGate(resumed=None)
        engine = _StubEngine(gate)
        service = self._service(engine)

        await service.dispatch_resume(
            approval_id="approval-1",
            approved=False,
            decided_by="admin",
            decision_reason=None,
        )
        await service.drain_resume_tasks()

        engine.resume_parked_run.assert_not_awaited()

    async def test_dispatch_missing_approval_gate_fails_loud(self) -> None:
        """gate is None -> fail loud, never silently strand the run.

        The decision is already persisted by the controller, so the
        resume must surface APPROVAL_GATE_RESUME_FAILED (via the
        background-task registry) and must NOT proceed into
        ``resume_parked_run`` rather than returning a successful no-op.
        """
        engine = _StubEngine(gate=None)
        service = self._service(engine)

        with capture_logs() as logs:
            await service.dispatch_resume(
                approval_id="approval-1",
                approved=True,
                decided_by="admin",
                decision_reason=None,
            )
            await service.drain_resume_tasks()

        engine.resume_parked_run.assert_not_awaited()
        failed = [
            e
            for e in logs
            if e.get("event") == APPROVAL_GATE_RESUME_FAILED
            and e.get("reason") == "engine_has_no_approval_gate"
        ]
        assert failed, "missing-gate resume did not log a loud failure"

    async def test_no_provider_dispatch_resume_rejects(self) -> None:
        service = NoProviderExecutionService()
        with pytest.raises(AgentRuntimeNotConfiguredError, match="no"):
            await service.dispatch_resume(
                approval_id="approval-1",
                approved=True,
                decided_by="admin",
                decision_reason=None,
            )

    async def test_lifecycle_baseline_dispatch_resume_rejects(self) -> None:
        service = LifecycleAdvancingExecutionService(
            task_engine=mock_of[TaskEngine](),
        )
        with pytest.raises(
            AgentRuntimeNotConfiguredError,
            match="not installed",
        ):
            await service.dispatch_resume(
                approval_id="approval-1",
                approved=True,
                decided_by="admin",
                decision_reason=None,
            )


class TestSandboxOwnerRelease:
    """The task boundary releases the sandbox lifecycle owner."""

    async def _service(
        self,
        *,
        sandbox_backend: SandboxBackend | None,
        strategy_kind: str,
    ) -> tuple[AgentEngineExecutionService, object, object]:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        post = task.model_copy(update={"status": TaskStatus.IN_REVIEW})
        registry = AgentRegistryService()
        await registry.register(identity)
        service = AgentEngineExecutionService(
            engine=mock_of[AgentEngine](
                run=AsyncMock(return_value=_run_result()),
            ),
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(side_effect=[task, post]),
            ),
            agent_registry=registry,
            sandbox_backend=sandbox_backend,
            lifecycle_strategy_kind=strategy_kind,
        )
        return service, identity, task

    async def _run(self, service: AgentEngineExecutionService, task: object) -> None:
        await service.execute_once(
            task_id=task.id,  # type: ignore[attr-defined]
            previous_status="assigned",
            new_status="in_progress",
            idempotency_key="k",
            requested_by="user",
        )

    async def test_per_agent_releases_agent_id(self) -> None:
        release = AsyncMock()
        backend = mock_of[SandboxBackend](release_owner=release)
        service, identity, task = await self._service(
            sandbox_backend=backend,
            strategy_kind=STRATEGY_PER_AGENT,
        )
        await self._run(service, task)
        release.assert_awaited_once_with(str(identity.id))  # type: ignore[attr-defined]

    async def test_per_task_releases_task_id(self) -> None:
        release = AsyncMock()
        backend = mock_of[SandboxBackend](release_owner=release)
        service, _identity, task = await self._service(
            sandbox_backend=backend,
            strategy_kind=STRATEGY_PER_TASK,
        )
        await self._run(service, task)
        release.assert_awaited_once_with(task.id)  # type: ignore[attr-defined]

    async def test_per_call_does_not_release(self) -> None:
        release = AsyncMock()
        backend = mock_of[SandboxBackend](release_owner=release)
        service, _identity, task = await self._service(
            sandbox_backend=backend,
            strategy_kind=STRATEGY_PER_CALL,
        )
        await self._run(service, task)
        release.assert_not_awaited()

    async def test_no_backend_is_a_noop(self) -> None:
        service, _identity, task = await self._service(
            sandbox_backend=None,
            strategy_kind=STRATEGY_PER_AGENT,
        )
        # Must not raise despite no backend wired.
        await self._run(service, task)

    async def test_release_failure_is_swallowed(self) -> None:
        release = AsyncMock(side_effect=RuntimeError("docker gone"))
        backend = mock_of[SandboxBackend](release_owner=release)
        service, _identity, task = await self._service(
            sandbox_backend=backend,
            strategy_kind=STRATEGY_PER_TASK,
        )
        # A failing release must not fail an otherwise-good task.
        await self._run(service, task)
        release.assert_awaited_once()
