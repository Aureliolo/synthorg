"""Acceptance: the agent runtime is online behind the provider switch.

Drives a real task through the ``WorkerExecutionService.execute_once``
seam built by the production ``build_runtime_services`` (the exact
code the boot hook runs) -- not ``AgentEngine.run`` directly.
With a provider configured a real agent runs via the deterministic
``ScriptedDriver`` and the minimal safety spine is exercised end to
end: the SecOps interceptor consults the autonomy/rule verdict on the
tool action and a sensitive action (LOCKED autonomy) produces an
approval-queue entry, parking the task. Nothing runs ungoverned.

The boot hook installing this service into the live app at startup is
covered by ``tests/integration/api/test_runtime_install_ordering.py``;
this test isolates the runtime so the Litestar lifespan / Windows IOCP
teardown cannot mask the behaviour under assertion.
"""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from synthorg.api.approval_store import ApprovalStore
from synthorg.config.schema import RootConfig
from synthorg.core.agent import ToolPermissions
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.clock import SystemClock
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.tool_constraints import ToolAccessLevel
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import CreateTaskData
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability.events.security import (
    SECURITY_ESCALATION_CREATED,
    SECURITY_EVALUATE_START,
    SECURITY_VERDICT_ESCALATE,
)
from synthorg.observability.events.workers import (
    WORKERS_EXECUTION_SERVICE_AGENT_RUN,
)
from synthorg.providers.drivers.scripted import (
    ScriptedDriver,
    SequencedResponseStrategy,
)
from synthorg.providers.models import ToolCall
from synthorg.providers.registry import ProviderRegistry
from synthorg.settings.registry import get_registry
from synthorg.settings.resolver import ConfigResolver
from synthorg.settings.service import SettingsService
from synthorg.workers.execution_service import AgentEngineExecutionService
from synthorg.workers.runtime_builder import build_runtime_services
from tests._shared import make_app_state
from tests._shared.scripted_provider import (
    make_e2e_identity,
    make_tool_call_response,
)
from tests.unit.api.fakes import FakePersistenceBackend

pytestmark = pytest.mark.e2e

_SECOPS_EVENTS = {
    SECURITY_EVALUATE_START,
    SECURITY_VERDICT_ESCALATE,
    SECURITY_ESCALATION_CREATED,
}


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


async def test_runtime_executes_task_through_seam_with_safety_spine(
    persistence: FakePersistenceBackend,
    task_engine: TaskEngine,
    tmp_path: Path,
) -> None:
    # Scripted agent: turn 1 asks to list a directory. Under LOCKED
    # autonomy the SecOps interceptor routes that tool action to the
    # approval queue and the loop parks -- no second LLM turn needed.
    tool_call = make_tool_call_response(
        tool_calls=(ToolCall(id="c1", name="list_directory", arguments={"path": "."}),),
    )
    provider = ScriptedDriver(
        "test-provider", strategy=SequencedResponseStrategy((tool_call,))
    )
    registry = ProviderRegistry({"test-provider": provider})
    approval_store = ApprovalStore()
    agent_registry = AgentRegistryService()
    identity = make_e2e_identity(
        tools=ToolPermissions(access_level=ToolAccessLevel.ELEVATED),
    ).model_copy(update={"autonomy_level": AutonomyLevel.LOCKED})
    await agent_registry.register(identity)

    root_config = RootConfig(company_name="runtime-online-test")
    settings_service = SettingsService(
        repository=persistence.settings,
        registry=get_registry(),
    )
    config_resolver = ConfigResolver(
        settings_service=settings_service,
        config=root_config,
    )
    # No boot-shared gate / trust service is wired here, so the engine
    # builds its own ApprovalGate from ``approval_store`` (the path this
    # acceptance test exercises). The approval-gate and trust-service
    # slice fields stay unset (``None``), so the store-backed fallback
    # is what runs.
    app_state = make_app_state(
        provider_registry=registry,
        config=root_config,
        config_resolver=config_resolver,
        task_engine=task_engine,
        agent_registry=agent_registry,
        approval_store=approval_store,
        clock=SystemClock(),
        agent_workspace_root=tmp_path,
    )

    runtime = await build_runtime_services(
        app_state,
        workspace_root=tmp_path,
    )
    service = runtime.worker_execution_service
    assert isinstance(service, AgentEngineExecutionService)

    created = await task_engine.create_task(
        CreateTaskData(
            title="List the workspace",
            description="Inspect the working directory.",
            type=TaskType.DEVELOPMENT,
            project="proj-runtime",
            created_by="operator",
        ),
        requested_by="operator",
    )
    await task_engine.transition_task(
        str(created.id),
        TaskStatus.ASSIGNED,
        requested_by="operator",
        assigned_to=str(identity.id),
    )

    with capture_logs() as logs:
        post = await service.execute_once(
            task_id=str(created.id),
            previous_status="created",
            new_status="assigned",
            idempotency_key="idem-1",
            requested_by="worker",
        )

    # A real agent ran and the task came back (post-run state).
    assert post.id == created.id

    # The loop parked: the approval gate fired (the hard signal that
    # the spine escalated, not that some approval tool was invoked).
    agent_run = [
        e for e in logs if e.get("event") == WORKERS_EXECUTION_SERVICE_AGENT_RUN
    ]
    assert agent_run, "agent run was not logged -- runtime not online"
    # The agent actually executed LLM turns (not a zero-turn no-op):
    # direct proof the runtime ran, not merely that the spine fired.
    assert agent_run[0]["total_turns"] >= 1
    assert agent_run[0]["termination_reason"] == TerminationReason.PARKED.value

    # The SecOps interceptor consulted the verdict (distinct from the
    # request_human_approval tool path -- the script never called it).
    assert any(e.get("event") in _SECOPS_EVENTS for e in logs), (
        "no SecOps interceptor event -- spine not consulted"
    )

    # A sensitive action produced an approval-queue entry, and it came
    # from the SecOps escalation (the scripted tool was list_directory,
    # never request_human_approval).
    items = await approval_store.list_items()
    assert len(items) >= 1
    assert all("request_human_approval" not in i.action_type for i in items)
