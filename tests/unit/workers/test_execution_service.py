# module-kind: tests
"""Unit tests for the agent-runtime worker execution services."""

from unittest.mock import AsyncMock, Mock

import pytest
from structlog.testing import capture_logs

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.domain_errors import (
    AgentRuntimeNotConfiguredError,
    ConflictError,
    NotFoundError,
)
from synthorg.core.effective_autonomy import EffectiveAutonomy
from synthorg.core.persistence_errors import QueryError
from synthorg.core.project import Project
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.context import AgentContext
from synthorg.engine.errors import ProjectWorkspaceNotProvisionedError
from synthorg.engine.health.pipeline import HealthMonitoringPipeline
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.engine.prompt import SystemPrompt
from synthorg.engine.quality.models import StepQuality, StepQualitySignal
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_FAILED,
)
from synthorg.observability.events.workers import (
    WORKERS_EXECUTION_SERVICE_AUTONOMY_DEGRADED,
    WORKERS_EXECUTION_SERVICE_FAILED,
    WORKERS_EXECUTION_SERVICE_HEALTH_PIPELINE_FAILED,
)
from synthorg.persistence.project_protocol import ProjectRepository
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
from tests._shared import StubWorkPipeline, as_uuid, mock_of, task_from_work_item
from tests._shared.scripted_provider import make_e2e_identity, make_e2e_task

pytestmark = pytest.mark.unit


def _run_result() -> object:
    return mock_of[AgentRunResult](
        termination_reason=TerminationReason.COMPLETED,
        total_turns=1,
    )


class TestProjectAutonomyModeResolution:
    """The per-initiative mode read + its fail-closed degrade + resolver glue."""

    def _service(
        self,
        *,
        project_repo: ProjectRepository | None,
        autonomy_resolver: AutonomyResolver | None = None,
    ) -> AgentEngineExecutionService:
        return AgentEngineExecutionService(
            engine=mock_of[AgentEngine](run=AsyncMock()),
            task_engine=mock_of[TaskEngine](),
            agent_registry=AgentRegistryService(),
            autonomy_resolver=autonomy_resolver
            or AutonomyResolver(registry=ActionTypeRegistry(), config=AutonomyConfig()),
            project_repo=project_repo,
        )

    async def test_returns_the_projects_configured_mode(self) -> None:
        service = self._service(
            project_repo=mock_of[ProjectRepository](
                get=AsyncMock(
                    return_value=Project(
                        name="Init", autonomy_mode=AutonomyLevel.LOCKED
                    )
                )
            )
        )
        resolved = await service._resolve_project_autonomy_mode(NotBlankStr("p1"))
        assert resolved == AutonomyLevel.LOCKED

    async def test_no_override_configured_returns_none(self) -> None:
        service = self._service(
            project_repo=mock_of[ProjectRepository](
                get=AsyncMock(return_value=Project(name="Init", autonomy_mode=None))
            )
        )
        assert await service._resolve_project_autonomy_mode(NotBlankStr("p1")) is None

    async def test_absent_project_fails_closed_to_locked(self) -> None:
        service = self._service(
            project_repo=mock_of[ProjectRepository](get=AsyncMock(return_value=None))
        )
        with capture_logs() as logs:
            resolved = await service._resolve_project_autonomy_mode(NotBlankStr("p1"))
        # A task naming a project_id whose row is absent is anomalous; fail
        # CLOSED rather than silently inheriting a looser company default.
        assert resolved == AutonomyLevel.LOCKED
        degraded = [
            log
            for log in logs
            if log["event"] == WORKERS_EXECUTION_SERVICE_AUTONOMY_DEGRADED
        ]
        assert len(degraded) == 1
        assert degraded[0]["reason"] == "project_not_found"
        assert degraded[0]["fail_closed_to"] == "locked"

    async def test_no_repo_wired_returns_none(self) -> None:
        service = self._service(project_repo=None)
        assert await service._resolve_project_autonomy_mode(NotBlankStr("p1")) is None

    async def test_lookup_failure_fails_closed_to_locked(self) -> None:
        service = self._service(
            project_repo=mock_of[ProjectRepository](
                get=AsyncMock(side_effect=QueryError("db down"))
            )
        )
        with capture_logs() as logs:
            resolved = await service._resolve_project_autonomy_mode(NotBlankStr("p1"))
        # Fail CLOSED: a lookup miss must never silently loosen oversight.
        assert resolved == AutonomyLevel.LOCKED
        degraded = [
            log
            for log in logs
            if log["event"] == WORKERS_EXECUTION_SERVICE_AUTONOMY_DEGRADED
        ]
        assert len(degraded) == 1
        assert degraded[0]["fail_closed_to"] == "locked"

    async def test_resolve_autonomy_threads_project_level_into_resolver(self) -> None:
        resolver = mock_of[AutonomyResolver](
            resolve=Mock(return_value=mock_of[EffectiveAutonomy]())
        )
        service = self._service(
            project_repo=mock_of[ProjectRepository](
                get=AsyncMock(
                    return_value=Project(
                        name="Init", autonomy_mode=AutonomyLevel.SUPERVISED
                    )
                )
            ),
            autonomy_resolver=resolver,
        )
        await service._resolve_autonomy(
            make_e2e_identity(), task_id="t1", project_id=NotBlankStr("p1")
        )
        _, kwargs = resolver.resolve.call_args
        assert kwargs["project_level"] == AutonomyLevel.SUPERVISED


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
                task_id=str(task.id),
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
                task_id=str(task.id),
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
                task_id=str(task.id),
                previous_status="assigned",
                new_status="in_progress",
                idempotency_key="k",
                requested_by="user",
            )

        assert any(
            entry.get("log_level") == "error"
            and entry.get("event") == WORKERS_EXECUTION_SERVICE_FAILED
            and entry.get("task_id") == str(task.id)
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
            task_id=str(task.id),
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

    async def test_workspace_provision_failure_fails_loud(self) -> None:
        # A broken persistent workspace must fail the task with a surfaced
        # reason (raise), never silently degrade to a no-workspace run that
        # then cascades into an empty result masquerading as success. The
        # agent must never run.
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        registry = AgentRegistryService()
        await registry.register(identity)
        engine_run = AsyncMock(return_value=_run_result())
        task_engine = mock_of[TaskEngine](get_task=AsyncMock(return_value=task))
        workspace_service = mock_of[ProjectWorkspaceService](
            get_or_provision=AsyncMock(
                side_effect=ProjectWorkspaceNotProvisionedError(
                    project_id=NotBlankStr("proj-e2e")
                )
            )
        )
        service = AgentEngineExecutionService(
            engine=mock_of[AgentEngine](run=engine_run),
            task_engine=task_engine,
            agent_registry=registry,
            autonomy_resolver=AutonomyResolver(
                registry=ActionTypeRegistry(),
                config=AutonomyConfig(),
            ),
            project_workspace_service=workspace_service,
        )

        with capture_logs() as logs, pytest.raises(ProjectWorkspaceNotProvisionedError):
            await service.execute_once(
                task_id=str(task.id),
                previous_status="assigned",
                new_status="in_progress",
                idempotency_key="k",
                requested_by="user",
            )

        engine_run.assert_not_awaited()
        assert any(
            entry.get("event") == WORKERS_EXECUTION_SERVICE_FAILED
            and entry.get("reason") == "project_workspace_provision_failed"
            and entry.get("task_id") == str(task.id)
            for entry in logs
        )

    async def test_health_gate_failure_does_not_disrupt_completion(self) -> None:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        post = task.model_copy(update={"status": TaskStatus.IN_REVIEW})
        registry = AgentRegistryService()
        await registry.register(identity)
        engine_run = AsyncMock(return_value=_run_result())
        task_engine = mock_of[TaskEngine](
            get_task=AsyncMock(side_effect=[task, post]),
        )

        async def _raising_health_enabled() -> bool:
            msg = "health flag boom"
            raise RuntimeError(msg)

        service = AgentEngineExecutionService(
            engine=mock_of[AgentEngine](run=engine_run),
            task_engine=task_engine,
            agent_registry=registry,
            autonomy_resolver=AutonomyResolver(
                registry=ActionTypeRegistry(),
                config=AutonomyConfig(),
            ),
            health_pipeline=mock_of[HealthMonitoringPipeline](process=AsyncMock()),
            health_enabled=_raising_health_enabled,
        )

        with capture_logs() as logs:
            result = await service.execute_once(
                task_id=str(task.id),
                previous_status="assigned",
                new_status="in_progress",
                idempotency_key="k",
                requested_by="user",
            )

        # The completion path still returns the post-run state: a failing
        # health-gate read must not abort ``execute_once`` after the agent ran.
        assert result.status == TaskStatus.IN_REVIEW
        assert any(
            entry.get("log_level") == "warning"
            and entry.get("event") == WORKERS_EXECUTION_SERVICE_HEALTH_PIPELINE_FAILED
            and entry.get("task_id") == str(task.id)
            and entry.get("error_type") == "RuntimeError"
            for entry in logs
        )

    async def test_quality_signals_forwarded_to_health_pipeline(self) -> None:
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        post = task.model_copy(update={"status": TaskStatus.IN_REVIEW})
        registry = AgentRegistryService()
        await registry.register(identity)
        signal = StepQualitySignal(
            quality=StepQuality.CORRECT,
            confidence=0.7,
            reason="step ok",
            step_index=0,
            turn_range=(1, 1),
        )
        run_result = AgentRunResult(
            execution_result=ExecutionResult(
                context=AgentContext.from_identity(identity),
                termination_reason=TerminationReason.COMPLETED,
                quality_signals=(signal,),
            ),
            system_prompt=SystemPrompt(
                content="Test prompt",
                template_version="1.0",
                estimated_tokens=10,
                sections=("identity",),
                metadata={},
            ),
            duration_seconds=0.0,
            agent_id=str(identity.id),
            task_id=str(task.id),
            currency="USD",
        )
        process_mock = AsyncMock()
        service = AgentEngineExecutionService(
            engine=mock_of[AgentEngine](run=AsyncMock(return_value=run_result)),
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(side_effect=[task, post]),
            ),
            agent_registry=registry,
            autonomy_resolver=AutonomyResolver(
                registry=ActionTypeRegistry(),
                config=AutonomyConfig(),
            ),
            health_pipeline=mock_of[HealthMonitoringPipeline](process=process_mock),
        )

        await service.execute_once(
            task_id=str(task.id),
            previous_status="assigned",
            new_status="in_progress",
            idempotency_key="k",
            requested_by="user",
        )

        process_mock.assert_awaited_once()
        await_args = process_mock.await_args
        assert await_args is not None
        assert await_args.kwargs["quality_signals"] == (signal,)

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
            task_id=str(task.id),
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
            task_id=str(task.id),
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
            task_id=str(task.id),
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
                task_id=str(task.id),
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
    """Minimal AgentEngine surface for dispatch tests."""

    def __init__(self, gate: object) -> None:
        self._approval_gate = gate
        self.resume_parked_run = AsyncMock(return_value=_run_result())
        self.resume_parked_chat_action = AsyncMock()
        self.project_background_failure = AsyncMock()


class TestDispatchConversationalExecution:
    """The backgrounded spine surfaces an early failure onto the SSE stream."""

    def _service(self, engine: object) -> AgentEngineExecutionService:
        return AgentEngineExecutionService(
            engine=engine,  # type: ignore[arg-type]
            task_engine=mock_of[TaskEngine](),
            agent_registry=AgentRegistryService(),
        )

    def _work_item(self) -> WorkItem:
        return WorkItem(
            origin_adapter_id=NotBlankStr("conversational-cos"),
            source=WorkSource.CONVERSATIONAL,
            title=NotBlankStr("Build the landing page"),
            raw_intent=NotBlankStr("Create the marketing page"),
            project=NotBlankStr("marketing"),
            requested_by=NotBlankStr("user-1"),
        )

    async def test_spine_failure_projects_run_error(self) -> None:
        engine = _StubEngine(gate=None)
        service = self._service(engine)
        work_item = self._work_item()
        task = task_from_work_item(work_item)
        pipeline = StubWorkPipeline(continue_error=RuntimeError("project gone"))

        service.dispatch_conversational_execution(
            work_pipeline=pipeline, work_item=work_item, task=task
        )
        await service.drain_resume_tasks()

        engine.project_background_failure.assert_awaited_once_with(
            task_id=str(task.id), agent_id="system:pipeline"
        )

    async def test_success_projects_no_terminal_error(self) -> None:
        engine = _StubEngine(gate=None)
        service = self._service(engine)
        work_item = self._work_item()
        task = task_from_work_item(work_item)
        pipeline = StubWorkPipeline()

        service.dispatch_conversational_execution(
            work_pipeline=pipeline, work_item=work_item, task=task
        )
        await service.drain_resume_tasks()

        assert pipeline.continue_calls == [(work_item, task)]
        engine.project_background_failure.assert_not_awaited()


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

    async def test_dispatch_resumes_taskless_chat_action(self) -> None:
        """A taskless parked context routes to the chat-action resume.

        Direct-MCP chat actions park a context with no
        ``task_execution``; the worker must dispatch them to
        ``resume_parked_chat_action`` (which skips the task-only
        workspace/sandbox provisioning) rather than ``resume_parked_run``
        (which rejects taskless contexts).
        """
        from synthorg.engine.context import AgentContext

        identity = make_e2e_identity()
        ctx = AgentContext.from_identity(identity)
        assert ctx.task_execution is None
        gate = _StubGate(resumed=(ctx, "parked-chat-1"))
        engine = _StubEngine(gate)
        service = self._service(engine)

        await service.dispatch_resume(
            approval_id="appr-chat-1",
            approved=True,
            decided_by="operator-1",
            decision_reason=None,
        )
        await service.drain_resume_tasks()

        engine.resume_parked_run.assert_not_awaited()
        engine.resume_parked_chat_action.assert_awaited_once()
        call = engine.resume_parked_chat_action.await_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs["parked_context"] is ctx
        assert kwargs["approval_id"] == "appr-chat-1"
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


class TestLifecycleBaselineCompletionFence:
    """The oracle-less baseline never completes plan-linked work."""

    @staticmethod
    def _service(task: object) -> LifecycleAdvancingExecutionService:
        return LifecycleAdvancingExecutionService(
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(return_value=task),
                transition_task=AsyncMock(
                    side_effect=AssertionError("no transition expected"),
                ),
            ),
        )

    @staticmethod
    async def _advance(service: LifecycleAdvancingExecutionService) -> object:
        return await service.execute_once(
            task_id=str(as_uuid("task-1")),
            previous_status=None,
            new_status=TaskStatus.IN_REVIEW.value,
            idempotency_key="key-1",
            requested_by="worker-1",
        )

    async def test_plan_linked_task_stops_at_in_review(self) -> None:
        """A plan-linked IN_REVIEW task is returned unchanged, not completed.

        Initiative completion is derived from persisted task status, and that
        derivation is only honest because COMPLETED means the review gate's
        oracle chain passed. This baseline runs no oracle, so it must leave
        the verdict to the gate.
        """
        task = make_e2e_task(identity=make_e2e_identity()).model_copy(
            update={"status": TaskStatus.IN_REVIEW, "plan_id": as_uuid("plan-1")},
        )
        service = self._service(task)

        with capture_logs() as logs:
            result = await self._advance(service)

        assert result is task
        assert [
            entry
            for entry in logs
            if entry.get("reason") == "plan_linked_needs_review_gate"
        ]

    async def test_unplanned_task_still_completes(self) -> None:
        """A directly filed task keeps the baseline's full happy path."""
        task = make_e2e_task(identity=make_e2e_identity()).model_copy(
            update={"status": TaskStatus.IN_REVIEW}
        )
        completed = task.model_copy(update={"status": TaskStatus.COMPLETED})
        service = LifecycleAdvancingExecutionService(
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(return_value=task),
                transition_task=AsyncMock(return_value=(completed, 2)),
            ),
        )

        result = await self._advance(service)

        assert result is completed


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
            task_id=str(task.id),  # type: ignore[attr-defined]
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
        release.assert_awaited_once_with(
            str(identity.id),  # type: ignore[attr-defined]
            project_id=task.project,  # type: ignore[attr-defined]
            image_override=None,
        )

    async def test_per_task_releases_task_id(self) -> None:
        release = AsyncMock()
        backend = mock_of[SandboxBackend](release_owner=release)
        service, _identity, task = await self._service(
            sandbox_backend=backend,
            strategy_kind=STRATEGY_PER_TASK,
        )
        await self._run(service, task)
        release.assert_awaited_once_with(
            str(task.id),  # type: ignore[attr-defined]
            project_id=task.project,  # type: ignore[attr-defined]
            image_override=None,
        )

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

    async def test_release_runs_when_engine_run_raises(self) -> None:
        # Contract: release at the task boundary regardless of outcome.
        # A failing agent run must still release the sandbox owner.
        release = AsyncMock()
        backend = mock_of[SandboxBackend](release_owner=release)
        identity = make_e2e_identity()
        task = make_e2e_task(identity=identity)
        registry = AgentRegistryService()
        await registry.register(identity)
        service = AgentEngineExecutionService(
            engine=mock_of[AgentEngine](
                run=AsyncMock(side_effect=RuntimeError("engine boom")),
            ),
            task_engine=mock_of[TaskEngine](
                get_task=AsyncMock(return_value=task),
            ),
            agent_registry=registry,
            sandbox_backend=backend,
            lifecycle_strategy_kind=STRATEGY_PER_TASK,
        )
        with pytest.raises(RuntimeError, match="engine boom"):
            await service.execute_once(
                task_id=str(task.id),
                previous_status="assigned",
                new_status="in_progress",
                idempotency_key="k",
                requested_by="user",
            )
        release.assert_awaited_once_with(
            str(task.id), project_id=task.project, image_override=None
        )
