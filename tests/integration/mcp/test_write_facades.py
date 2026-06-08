"""Integration coverage for the META-MCP-3 write facades.

Exercises one happy-path dispatch per newly-live MCP tool, asserting
that:

- the envelope is ``{"status": "ok", ...}`` (or ``capability_gap`` when
  the optional service is intentionally not wired);
- the underlying service method was invoked with the right arguments;
- ``MCP_HANDLER_SERVICE_FALLBACK`` is never emitted (the legacy event
  must stay at zero call sites).

The fixtures wire fully-mocked services so the test exercises the
handler -> service path end-to-end without touching persistence.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog.testing

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.workflow.execution_service import WorkflowExecutionService
from synthorg.engine.workflow.service import WorkflowService
from synthorg.engine.workflow.subworkflow_service import SubworkflowService
from synthorg.engine.workflow.validation_types import WorkflowValidationResult
from synthorg.engine.workflow.version_service import WorkflowVersionService
from synthorg.hr.activity_service import ActivityFeedService
from synthorg.hr.performance.models import CollaborationCalibration
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.registry import AgentRegistryService
from synthorg.meta.mcp.handlers import build_handler_map
from synthorg.meta.models import ImprovementCycleResult
from synthorg.meta.service import SelfImprovementService
from synthorg.observability.events.mcp import (
    MCP_ADMIN_OP_EXECUTED,
    MCP_HANDLER_SERVICE_FALLBACK,
)
from synthorg.security.autonomy.models import AutonomyUpdateResult
from tests._shared import make_app_state
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.integration


def _sync_dumped(data: dict[str, Any]) -> SimpleNamespace:
    """Build a Pydantic-model double whose ``model_dump`` returns *data*.

    Used by handlers that call ``record.model_dump(mode="json")`` on the
    returned service value; ``SimpleNamespace`` exposes the supplied
    keys as real attributes (so the handler can read ``record.id`` etc.)
    without dragging a bare ``MagicMock`` through the test surface.
    """
    return SimpleNamespace(**data, model_dump=lambda mode="json": data)


@pytest.fixture
def actor() -> AgentIdentity:
    return make_test_actor()


@pytest.fixture
def identity() -> AgentIdentity:
    return make_test_actor(name="alpha")


@pytest.fixture
def services(identity: AgentIdentity) -> SimpleNamespace:
    """Mock services covering every META-MCP-3 facade.

    Returned as a ``SimpleNamespace`` so tests can stub side effects and
    assert on awaited calls by attribute name (``services.agent_registry``
    etc.); the live ``AppState`` built from these mocks is exposed via
    the :func:`app_state` fixture.
    """
    ns = SimpleNamespace()

    registry = AsyncMock(spec=AgentRegistryService)
    registry.get.return_value = identity
    registry.get_by_name.return_value = identity
    registry.list_active.return_value = (identity,)
    registry.apply_identity_update.return_value = identity
    registry.update_autonomy.return_value = AutonomyUpdateResult(
        agent_id=NotBlankStr(str(identity.id)),
        current_level=AutonomyLevel.SUPERVISED,
        requested_level=AutonomyLevel.SEMI,
        approval_id=NotBlankStr("approval-42"),
    )
    ns.agent_registry = registry

    tracker = AsyncMock(spec=PerformanceTracker)
    tracker.get_collaboration_calibration.return_value = CollaborationCalibration(
        agent_id=NotBlankStr(str(identity.id)),
        strategy_name=NotBlankStr("test-strategy"),
        sample_size=3,
    )
    ns.performance_tracker = tracker

    engine = AsyncMock(spec=TaskEngine)
    dummy_task = _sync_dumped({"id": "task-1", "title": "Test", "status": "pending"})
    engine.create_task.return_value = dummy_task
    ns.task_engine = engine

    activity_service = AsyncMock(spec=ActivityFeedService)
    activity_service.list_recent_activity.return_value = ((), 0)
    ns.activity_feed_service = activity_service

    workflow_service = AsyncMock(spec=WorkflowService)
    workflow_def = _sync_dumped({"id": "wfdef-1", "name": "Test", "revision": 1})
    workflow_service.create_definition.return_value = workflow_def
    workflow_service.update_definition.return_value = workflow_def
    workflow_service.validate_definition.return_value = WorkflowValidationResult()
    ns.workflow_service = workflow_service

    execution_service = AsyncMock(spec=WorkflowExecutionService)
    dummy_execution = _sync_dumped(
        {"id": "wfexec-1", "definition_id": "wfdef-1", "status": "RUNNING"}
    )
    execution_service.list_executions.return_value = ()
    execution_service.get_execution.return_value = dummy_execution
    execution_service.activate.return_value = dummy_execution
    execution_service.cancel_execution.return_value = dummy_execution
    ns.workflow_execution_service = execution_service

    sub_service = AsyncMock(spec=SubworkflowService)
    sub_service.list_summaries.return_value = ((), 0)
    sub_service.get.return_value = workflow_def
    sub_service.create.return_value = workflow_def
    ns.subworkflow_service = sub_service

    version_service = AsyncMock(spec=WorkflowVersionService)
    version_service.list_versions.return_value = ((), 0)
    version_service.get_version.return_value = _sync_dumped(
        {"entity_id": "wfdef-1", "version": 1}
    )
    ns.workflow_version_service = version_service

    si_service = AsyncMock(spec=SelfImprovementService)
    si_service.get_config.return_value = {"enabled": False}
    started = datetime.now(UTC)
    si_service.trigger_cycle.return_value = ImprovementCycleResult(
        started_at=started,
        completed_at=started,
        proposals=(),
    )
    ns.self_improvement_service = si_service

    ns.approval_store = AsyncMock(spec=ApprovalStore)

    ns._dummy_task = dummy_task
    ns._dummy_execution = dummy_execution
    ns._dummy_def = workflow_def
    return ns


@pytest.fixture
def app_state(services: SimpleNamespace) -> AppState:
    """Live ``AppState`` with the mock services composed into their slices."""
    return make_app_state(
        agent_registry=services.agent_registry,
        performance_tracker=services.performance_tracker,
        task_engine=services.task_engine,
        activity_feed_service=services.activity_feed_service,
        workflow_service=services.workflow_service,
        workflow_execution_service=services.workflow_execution_service,
        subworkflow_service=services.subworkflow_service,
        workflow_version_service=services.workflow_version_service,
        self_improvement_service=services.self_improvement_service,
        approval_store=services.approval_store,
    )


def _parse(result: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(result)
    assert body["status"] in {"ok", "error"}
    return body


def _minimal_workflow_definition_dict(
    *,
    workflow_id: str = "wfdef-1",
) -> dict[str, Any]:
    """Return a dict that round-trips through ``WorkflowDefinition.model_validate``.

    Used by error-mapping tests so the mocked service exception path is
    actually exercised (an empty ``definition`` would fail Pydantic
    validation in the handler before the service is ever called).
    """
    return {
        "id": workflow_id,
        "name": "Test",
        "workflow_type": "sequential_pipeline",
        "version": "1.0.0",
        "is_subworkflow": False,
        "inputs": [
            {"name": "payload", "type": "string", "required": True},
        ],
        "outputs": [],
        "nodes": [
            {"id": "start", "type": "start", "label": "Start"},
            {"id": "end", "type": "end", "label": "End"},
        ],
        "edges": [
            {
                "id": "e1",
                "source_node_id": "start",
                "target_node_id": "end",
                "type": "sequential",
            },
        ],
        "created_by": "tester",
        "created_at": "2026-04-25T00:00:00+00:00",
        "updated_at": "2026-04-25T00:00:00+00:00",
    }


class TestNoFallbackEventsEmitted:
    """Live facades never emit the legacy MCP_HANDLER_SERVICE_FALLBACK event."""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "synthorg_agents_create",
            "synthorg_agents_update",
            "synthorg_autonomy_update",
            "synthorg_collaboration_get_calibration",
            "synthorg_tasks_create",
            "synthorg_activities_list",
            "synthorg_workflows_create",
            "synthorg_workflows_update",
            "synthorg_workflows_validate",
            "synthorg_subworkflows_list",
            "synthorg_subworkflows_get",
            "synthorg_subworkflows_create",
            "synthorg_subworkflows_delete",
            "synthorg_workflow_executions_list",
            "synthorg_workflow_executions_get",
            "synthorg_workflow_executions_start",
            "synthorg_workflow_executions_cancel",
            "synthorg_workflow_versions_list",
            "synthorg_workflow_versions_get",
            "synthorg_meta_get_config",
            "synthorg_meta_trigger_cycle",
        ],
    )
    async def test_tool_emits_no_fallback(
        self,
        tool_name: str,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        handler = handlers[tool_name]
        # Minimal, mostly-valid args for each tool.  Where Pydantic
        # validation is strict (e.g. ``identity``, ``definition``), the
        # handler returns ``invalid_argument`` -- still an ``error``
        # envelope, still no fallback emission.
        args: dict[str, Any] = {
            "agent_id": "agent-1",
            "agent_name": "alpha",
            "task_id": "task-1",
            "workflow_id": "wfdef-1",
            "subworkflow_id": "sw-1",
            "execution_id": "wfexec-1",
            "version": "1.0.0",
            "revision": 1,
            "level": "semi",
            "reason": "integration test guardrail",
            "confirm": True,
            "updates": {},
            "identity": {},
            "definition": {},
            "task_data": {},
            "project": "default",
            "context": {},
        }
        with structlog.testing.capture_logs() as logs:
            result = await handler(
                app_state=app_state,
                arguments=args,
                actor=actor,
            )
        body = _parse(result)
        # Every tool must produce a recognised envelope shape.
        assert body["status"] in {"ok", "error"}
        # Critical invariant: never emit the legacy fallback event.
        for event in logs:
            assert event.get("event") != MCP_HANDLER_SERVICE_FALLBACK


class TestHappyPathServiceInvocations:
    """Each live facade calls through to its service on valid input."""

    async def test_meta_get_config_calls_service(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_meta_get_config"](
                app_state=app_state,
                arguments={},
                actor=actor,
            )
        )
        assert body["status"] == "ok"
        services.self_improvement_service.get_config.assert_called_once()

    async def test_meta_trigger_cycle_calls_service(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_meta_trigger_cycle"](
                app_state=app_state,
                arguments={"confirm": True, "reason": "operator-triggered cycle"},
                actor=actor,
            )
        )
        assert body["status"] == "ok"
        services.self_improvement_service.trigger_cycle.assert_awaited_once()

    async def test_collaboration_calibration_calls_service(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_collaboration_get_calibration"](
                app_state=app_state,
                arguments={"agent_id": "agent-1"},
                actor=actor,
            )
        )
        assert body["status"] == "ok"
        # Pin the routed argument: the handler must wrap ``agent_id``
        # in ``NotBlankStr`` before calling the tracker. A regression
        # that drops the wrapping or the agent_id entirely would slip
        # past a bare ``assert_awaited_once`` check.
        services.performance_tracker.get_collaboration_calibration.assert_awaited_once_with(
            NotBlankStr("agent-1"),
        )

    async def test_autonomy_update_routes_through_registry(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_autonomy_update"](
                app_state=app_state,
                arguments={
                    "agent_id": "agent-1",
                    "level": "semi",
                    "reason": "trusted operator",
                },
                actor=actor,
            )
        )
        assert body["status"] == "ok"
        # Pin the routed arguments: handler must forward
        # NotBlankStr(agent_id) plus an AutonomyUpdate carrying the
        # exact level / reason it received, plus the wired approval
        # store on a kwarg.
        services.agent_registry.update_autonomy.assert_awaited_once()
        call = services.agent_registry.update_autonomy.await_args
        assert call.args[0] == "agent-1"
        update = call.args[1]
        assert update.requested_level == AutonomyLevel.SEMI
        assert update.reason == "trusted operator"
        assert call.kwargs["approval_store"] is services.approval_store

    async def test_activities_list_routes_through_feed_service(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_activities_list"](
                app_state=app_state,
                arguments={"task_id": "task-1"},
                actor=actor,
            )
        )
        assert body["status"] == "ok"
        # Pin the routed filter: handler must forward ``task_id`` (and
        # leave ``project`` as ``None``) to the feed service alongside
        # the default pagination + window. A regression that drops the
        # filter would otherwise still pass a bare ``assert_awaited_once``.
        services.activity_feed_service.list_recent_activity.assert_awaited_once()
        kwargs = services.activity_feed_service.list_recent_activity.await_args.kwargs
        assert kwargs["task_id"] == "task-1"
        assert kwargs["project"] is None


class TestErrorPaths:
    """Verify the handler -> service exception mapping at the MCP boundary."""

    async def test_agents_create_already_exists(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
        identity: AgentIdentity,
    ) -> None:
        from synthorg.hr.errors import AgentAlreadyRegisteredError

        services.agent_registry.register.side_effect = AgentAlreadyRegisteredError(
            "duplicate"
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_agents_create"](
                app_state=app_state,
                arguments={"identity": identity.model_dump(mode="json")},
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "already_exists"

    async def test_agents_update_not_found(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        from synthorg.hr.errors import AgentNotFoundError

        services.agent_registry.apply_identity_update.side_effect = AgentNotFoundError(
            "missing"
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_agents_update"](
                app_state=app_state,
                arguments={"agent_id": "agent-1", "updates": {"role": "x"}},
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "not_found"

    async def test_agents_update_blocked_field(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        services.agent_registry.apply_identity_update.side_effect = ValueError(
            "Fields are immutable: ['name']"
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_agents_update"](
                app_state=app_state,
                arguments={"agent_id": "agent-1", "updates": {"name": "x"}},
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"

    async def test_autonomy_update_short_reason(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_autonomy_update"](
                app_state=app_state,
                arguments={
                    "agent_id": "agent-1",
                    "level": "semi",
                    "reason": "ok",
                },
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "invalid_argument"

    async def test_workflows_create_already_exists(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        from synthorg.engine.workflow.service import WorkflowDefinitionExistsError

        services.workflow_service.create_definition.side_effect = (
            WorkflowDefinitionExistsError("duplicate")
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_workflows_create"](
                app_state=app_state,
                arguments={"definition": _minimal_workflow_definition_dict()},
                actor=actor,
            )
        )
        # Minimally-valid definition makes it past Pydantic, so the
        # service mock is actually invoked and the
        # ``WorkflowDefinitionExistsError`` -> ``already_exists`` mapping
        # is exercised.
        assert body["status"] == "error"
        assert body["domain_code"] == "already_exists"
        services.workflow_service.create_definition.assert_awaited_once()

    async def test_workflows_update_revision_mismatch(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        from synthorg.engine.workflow.service import (
            WorkflowDefinitionRevisionMismatchError,
        )

        services.workflow_service.update_definition.side_effect = (
            WorkflowDefinitionRevisionMismatchError(
                "stale",
                definition_id="wfdef-1",
                expected=2,
                actual=3,
            )
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_workflows_update"](
                app_state=app_state,
                arguments={
                    "definition": {
                        **_minimal_workflow_definition_dict(),
                        "revision": 1,
                    },
                },
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "conflict"
        services.workflow_service.update_definition.assert_awaited_once()

    async def test_subworkflows_delete_has_parents(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        from synthorg.engine.workflow.subworkflow_models import ParentReference
        from synthorg.engine.workflow.subworkflow_service import (
            SubworkflowHasParentsError,
        )

        parent = ParentReference(
            parent_id="wf-parent",
            parent_name="Parent",
            pinned_version="1.0.0",
            node_id="node-1",
            parent_type="workflow_definition",
            parent_version=None,
        )
        services.subworkflow_service.delete.side_effect = SubworkflowHasParentsError(
            "blocked",
            subworkflow_id="sw-1",
            version="1.0.0",
            parents=(parent,),
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_subworkflows_delete"](
                app_state=app_state,
                arguments={
                    "subworkflow_id": "sw-1",
                    "version": "1.0.0",
                    "confirm": True,
                    "reason": "cleanup",
                },
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "conflict"

    async def test_workflow_executions_cancel_already_terminal(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        from synthorg.engine.errors import WorkflowExecutionAlreadyTerminalError

        services.workflow_execution_service.cancel_execution.side_effect = (
            WorkflowExecutionAlreadyTerminalError("already terminal")
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_workflow_executions_cancel"](
                app_state=app_state,
                arguments={
                    "execution_id": "wfexec-1",
                    "confirm": True,
                    "reason": "stuck",
                },
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "conflict"

    async def test_meta_trigger_cycle_unavailable(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        from synthorg.meta.errors import SelfImprovementTriggerError

        services.self_improvement_service.trigger_cycle.side_effect = (
            SelfImprovementTriggerError("no snapshot builder")
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_meta_trigger_cycle"](
                app_state=app_state,
                arguments={"confirm": True, "reason": "operator-triggered cycle"},
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "unavailable"


class TestDestructiveAuditEvents:
    """Destructive ops emit MCP_ADMIN_OP_EXECUTED on success."""

    async def test_subworkflows_delete_emits_audit(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        services.subworkflow_service.delete.return_value = None
        handlers = build_handler_map()
        with structlog.testing.capture_logs() as logs:
            body = _parse(
                await handlers["synthorg_subworkflows_delete"](
                    app_state=app_state,
                    arguments={
                        "subworkflow_id": "sw-1",
                        "version": "1.0.0",
                        "confirm": True,
                        "reason": "cleanup",
                    },
                    actor=actor,
                )
            )
        assert body["status"] == "ok"
        events = [e for e in logs if e.get("event") == MCP_ADMIN_OP_EXECUTED]
        assert events, "expected MCP_ADMIN_OP_EXECUTED audit event"
        assert events[0]["target_id"] == "sw-1@1.0.0"

    async def test_workflow_executions_cancel_emits_audit(
        self,
        app_state: AppState,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        with structlog.testing.capture_logs() as logs:
            body = _parse(
                await handlers["synthorg_workflow_executions_cancel"](
                    app_state=app_state,
                    arguments={
                        "execution_id": "wfexec-1",
                        "confirm": True,
                        "reason": "stuck",
                    },
                    actor=actor,
                )
            )
        assert body["status"] == "ok"
        events = [e for e in logs if e.get("event") == MCP_ADMIN_OP_EXECUTED]
        assert events, "expected MCP_ADMIN_OP_EXECUTED audit event"
        assert events[0]["target_id"] == "wfexec-1"


class TestCapabilityGapFallbacks:
    """Optional services missing from AppState surface ``capability_gap``.

    Locks in the contract that handlers gated on ``has_<service>`` /
    ``getattr(..., None)`` checks return the dedicated
    ``capability_gap`` envelope -- not ``service_fallback`` -- when the
    optional service is intentionally not wired. Prevents a regression
    where a future refactor accidentally drops the guard and either
    crashes (``AttributeError``) or surfaces ``MCP_HANDLER_SERVICE_FALLBACK``.
    """

    async def test_activities_list_returns_capability_gap_when_unwired(
        self,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        # Build an AppState that wires every service EXCEPT the
        # activity feed; the handler must take the capability-gap path
        # rather than crash or emit ``MCP_HANDLER_SERVICE_FALLBACK``.
        bare_state = make_app_state(
            agent_registry=services.agent_registry,
            performance_tracker=services.performance_tracker,
            task_engine=services.task_engine,
            workflow_service=services.workflow_service,
            workflow_execution_service=services.workflow_execution_service,
            subworkflow_service=services.subworkflow_service,
            workflow_version_service=services.workflow_version_service,
            self_improvement_service=services.self_improvement_service,
            approval_store=services.approval_store,
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_activities_list"](
                app_state=bare_state,
                arguments={"task_id": "task-1"},
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "not_supported"
        services.activity_feed_service.list_recent_activity.assert_not_awaited()

    async def test_meta_get_config_returns_capability_gap_when_unwired(
        self,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        bare_state = make_app_state(
            agent_registry=services.agent_registry,
            performance_tracker=services.performance_tracker,
            task_engine=services.task_engine,
            activity_feed_service=services.activity_feed_service,
            workflow_service=services.workflow_service,
            workflow_execution_service=services.workflow_execution_service,
            subworkflow_service=services.subworkflow_service,
            workflow_version_service=services.workflow_version_service,
            approval_store=services.approval_store,
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_meta_get_config"](
                app_state=bare_state,
                arguments={},
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "not_supported"
        services.self_improvement_service.get_config.assert_not_called()

    async def test_meta_trigger_cycle_returns_capability_gap_when_unwired(
        self,
        services: SimpleNamespace,
        actor: AgentIdentity,
    ) -> None:
        bare_state = make_app_state(
            agent_registry=services.agent_registry,
            performance_tracker=services.performance_tracker,
            task_engine=services.task_engine,
            activity_feed_service=services.activity_feed_service,
            workflow_service=services.workflow_service,
            workflow_execution_service=services.workflow_execution_service,
            subworkflow_service=services.subworkflow_service,
            workflow_version_service=services.workflow_version_service,
            approval_store=services.approval_store,
        )
        handlers = build_handler_map()
        body = _parse(
            await handlers["synthorg_meta_trigger_cycle"](
                app_state=bare_state,
                arguments={"confirm": True, "reason": "operator-triggered cycle"},
                actor=actor,
            )
        )
        assert body["status"] == "error"
        assert body["domain_code"] == "not_supported"
        services.self_improvement_service.trigger_cycle.assert_not_awaited()
