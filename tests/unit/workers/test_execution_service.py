"""Unit tests for the agent-runtime worker execution services."""

from unittest.mock import AsyncMock

import pytest

from synthorg.core.domain_errors import AgentRuntimeNotConfiguredError, NotFoundError
from synthorg.core.enums import TaskStatus
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.run_result import AgentRunResult
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.security.action_types import ActionTypeRegistry
from synthorg.security.autonomy.models import AutonomyConfig
from synthorg.security.autonomy.resolver import AutonomyResolver
from synthorg.workers.execution_service import (
    AgentEngineExecutionService,
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
