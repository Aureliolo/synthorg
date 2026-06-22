"""Acceptance sweep: every MCP handler is wired and returns a valid envelope.

Acceptance sweep over the full MCP handler surface.  It asserts:

1. **Handler count parity** -- every tool in the registry has a
   concrete entry in ``build_handler_map()``.
2. **No placeholder remains** -- invoking each handler with a basic
   arg set returns an envelope whose ``status`` is ``"ok"`` or
   ``"error"``, never ``"not_implemented"``.  The placeholder
   scaffold only fires for a newly registered tool that has not been
   given a real handler yet; the full tool surface is covered by real
   handlers, even if many of them return a structured ``not_supported``
   error envelope because the underlying service layer isn't yet
   exposed on ``app_state``.
3. **Destructive ops enforce guardrails** -- the canonical
   destructive-op list (defined inline) is callable without
   ``confirm``/``reason`` and returns ``domain_code="guardrail_violated"``.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.api.state import AppState
from synthorg.core.agent import AgentIdentity
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import (
    CollaborationCalibration,
    CollaborationScoreResult,
)
from synthorg.meta.mcp.domains import build_full_registry
from synthorg.meta.mcp.handlers import build_handler_map
from synthorg.security.autonomy.models import AutonomyUpdateResult
from tests._shared import JsonDict, make_app_state
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


DESTRUCTIVE_TOOLS: tuple[str, ...] = (
    "synthorg_agents_delete",
    "synthorg_approvals_reject",
    "synthorg_tasks_delete",
    "synthorg_tasks_cancel",
    "synthorg_workflows_delete",
    "synthorg_subworkflows_delete",
    "synthorg_workflow_executions_cancel",
    "synthorg_messages_delete",
    "synthorg_meetings_delete",
    "synthorg_connections_delete",
    "synthorg_webhooks_delete",
    "synthorg_memory_cancel_fine_tune",
    "synthorg_memory_rollback_checkpoint",
    "synthorg_memory_delete_checkpoint",
    "synthorg_memory_delete_entry",
    "synthorg_mcp_catalog_uninstall",
    "synthorg_oauth_remove_provider",
    "synthorg_clients_deactivate",
    "synthorg_artifacts_delete",
    "synthorg_settings_delete",
    "synthorg_backup_delete",
    "synthorg_backup_restore",
    "synthorg_users_delete",
    "synthorg_projects_delete",
    "synthorg_template_packs_uninstall",
    "synthorg_departments_delete",
    "synthorg_teams_delete",
)


def _sync_dumped(data: JsonDict) -> MagicMock:
    """Mock Pydantic-model-ish that returns a sync ``model_dump``."""
    mock = MagicMock()
    mock.model_dump = MagicMock(return_value=data)
    # Populate some attribute reads handlers may take.
    for k, v in data.items():
        setattr(mock, k, v)
    return mock


@pytest.fixture
def fake_app_state() -> AppState:
    """Wide-blast app_state covering every service a handler probes."""
    dummy_task = _sync_dumped({"id": "task-1", "title": "x"})

    # Repositories used by handlers that live-shim.
    defrepo = AsyncMock()
    defrepo.list_definitions.return_value = ()
    defrepo.get.return_value = None
    defrepo.delete.return_value = False
    from synthorg.persistence.fine_tune_protocol import (
        FineTuneCheckpointRepository,
        FineTuneRunRepository,
    )
    from synthorg.persistence.version_protocol import VersionRepository

    fine_tune_checkpoints = AsyncMock(spec=FineTuneCheckpointRepository)
    fine_tune_checkpoints.list_items_page.return_value = ((), 0)
    fine_tune_checkpoints.get.return_value = None
    persistence = SimpleNamespace(
        workflow_definitions=defrepo,
        workflow_versions=AsyncMock(spec=VersionRepository),
        budget_config_versions=_mkversion_repo(),
        fine_tune_checkpoints=fine_tune_checkpoints,
        fine_tune_runs=AsyncMock(spec=FineTuneRunRepository),
    )

    # Engine / registries / trackers.
    engine = AsyncMock()
    engine.list_tasks.return_value = ((), 0)
    engine.get_task.return_value = None
    engine.update_task.return_value = dummy_task
    engine.cancel_task.return_value = (dummy_task, None)
    engine.delete_task.return_value = True
    engine.transition_task.return_value = (dummy_task, None)

    registry = AsyncMock()
    registry.list_active.return_value = ()
    registry.get_by_name.return_value = None
    registry.get.return_value = None
    # Write methods need Pydantic-shaped returns so the handler's
    # ``.model_dump()`` does not blow up on AsyncMock returns.
    registry.apply_identity_update.return_value = make_test_actor(name="alpha")
    registry.update_autonomy.return_value = AutonomyUpdateResult(
        agent_id=NotBlankStr("agent-1"),
        current_level=AutonomyLevel.SUPERVISED,
        requested_level=AutonomyLevel.FULL,
    )

    performance_tracker = AsyncMock()
    performance_tracker.get_snapshot.return_value = None
    performance_tracker.get_collaboration_score.return_value = CollaborationScoreResult(
        score=0.0,
        strategy_name="test-strategy",
        confidence=0.5,
    )
    performance_tracker.get_collaboration_calibration.return_value = (
        CollaborationCalibration(
            agent_id=NotBlankStr("agent-1"),
            strategy_name=NotBlankStr("test-strategy"),
            sample_size=0,
        )
    )

    cost_tracker = AsyncMock()
    cost_tracker.get_records.return_value = ()
    cost_tracker.get_agent_cost.return_value = 0.0
    config_resolver = AsyncMock()
    config_resolver.get_budget_config.return_value = _sync_dumped(
        {"currency": "EUR"},
    )

    approval_store = AsyncMock()
    approval_store.list_items.return_value = ()
    approval_store.get.return_value = None

    return make_app_state(
        persistence=persistence,
        task_engine=engine,
        agent_registry=registry,
        performance_tracker=performance_tracker,
        cost_tracker=cost_tracker,
        config_resolver=config_resolver,
        approval_store=approval_store,
    )


def _mkversion_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.list_versions.return_value = ()
    repo.count_versions.return_value = 0
    repo.get_version.return_value = None
    return repo


@pytest.fixture
def actor() -> AgentIdentity:
    return make_test_actor()


class TestHandlerParity:
    """Registry and handler map stay in lockstep."""

    def test_every_tool_has_a_handler(self) -> None:
        registry = build_full_registry()
        handlers = build_handler_map()
        missing = set(registry.get_names()) - set(handlers.keys())
        assert not missing

    def test_no_orphan_handlers(self) -> None:
        registry = build_full_registry()
        handlers = build_handler_map()
        orphans = set(handlers.keys()) - set(registry.get_names())
        assert not orphans

    def test_total_tool_count_matches_plan(self) -> None:
        """Registry has exactly the documented 245-tool surface.

        Pinning to the exact count catches accidental tool removal
        *and* double-registration.  Bump this number only when the
        MCP tool surface is intentionally grown or shrunk (current
        composition: 219 baseline + 8 cockpit + 5 charter +
        1 query_feature_map + 1 demo + 8 project-brain +
        3 security risk-override).
        """
        registry = build_full_registry()
        assert registry.tool_count == 245


class TestNoPlaceholderInProduction:
    """No real call path returns the legacy placeholder envelope.

    ``status == "not_implemented"`` would indicate the old scaffold
    placeholder is still registered for that tool, which the wired-handler
    contract forbids.
    """

    @pytest.mark.parametrize(
        "tool_name",
        sorted(build_handler_map().keys()),
    )
    async def test_tool_returns_non_placeholder_envelope(
        self,
        tool_name: str,
        fake_app_state: AppState,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        handler = handlers[tool_name]
        # Blast argument set: common names handlers might read.  Every
        # handler is defensive about missing args, so passing too many
        # is harmless.
        args: JsonDict = {
            "agent_name": "alpha",
            "agent_id": "agent-1",
            "approval_id": "approval-1",
            "task_id": "task-1",
            "workflow_id": "wf-1",
            "subworkflow_id": "sw-1",
            "execution_id": "ex-1",
            "checkpoint_id": "cp-1",
            "version_num": 1,
            "title": "x",
            "description": "y",
            "action_type": "deploy",
            "risk_level": "medium",
            "confirm": True,
            "reason": "test",
            "name": "alpha",
            "role": "engineer",
            "department": "Engineering",
            "session_id": "sess-1",
            "level": "FULL",
            "updates": {},
            "target_status": "in_progress",
            "steps": [],
            "status": "pending",
        }
        result = await handler(
            app_state=fake_app_state,
            arguments=args,
            actor=actor,
        )
        body = json.loads(result)
        # A handler may legitimately return ``ok`` or ``error`` (any
        # domain_code).  Pinning to this set catches both the legacy
        # ``not_implemented`` scaffold and any other malformed status.
        assert body["status"] in {"ok", "error"}, (
            f"{tool_name} returned unexpected status {body['status']!r}"
        )


def _baseline_args(tool_name: str) -> JsonDict:
    """Build the minimal argument set that passes the id/path checks for
    a destructive tool, leaving guardrail fields unset so each test can
    probe the branch it targets."""
    args: JsonDict = {}
    target_key_guess = _guess_id_key(tool_name)
    if target_key_guess:
        args[target_key_guess] = "placeholder-id"
    # ``synthorg_memory_delete_entry`` validates two non-blank ids
    # (``agent_id`` then ``memory_id``) before the destructive guardrail
    # check fires.  The prefix-based id-key heuristic only covers a
    # single key per tool; seed the second one explicitly so the
    # guardrail-branch sweeps actually reach the guardrail.
    if tool_name == "synthorg_memory_delete_entry":
        args["agent_id"] = "placeholder-agent"
        args["memory_id"] = "placeholder-mem"
    return args


class TestDestructiveGuardrails:
    """Every destructive op rejects missing confirm/reason/actor + non-bool confirm.

    Each parametrised sweep covers the full destructive-op universe so
    a new destructive tool that's missed from the canonical list gets
    caught by the handler-parity tests, and a handler that drops any
    single guardrail branch is caught here.
    """

    @pytest.mark.parametrize("tool_name", DESTRUCTIVE_TOOLS)
    async def test_missing_confirm_is_blocked(
        self,
        tool_name: str,
        fake_app_state: AppState,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        handler = handlers[tool_name]
        args = _baseline_args(tool_name) | {"reason": "cleanup"}
        result = await handler(
            app_state=fake_app_state,
            arguments=args,
            actor=actor,
        )
        body = json.loads(result)
        assert body["status"] == "error", f"{tool_name} should have rejected"
        assert body["domain_code"] == "guardrail_violated", (
            f"{tool_name} returned {body.get('domain_code')!r}; "
            f"expected guardrail_violated"
        )

    @pytest.mark.parametrize("tool_name", DESTRUCTIVE_TOOLS)
    async def test_missing_reason_is_blocked(
        self,
        tool_name: str,
        fake_app_state: AppState,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        handler = handlers[tool_name]
        args = _baseline_args(tool_name) | {"confirm": True}
        result = await handler(
            app_state=fake_app_state,
            arguments=args,
            actor=actor,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "guardrail_violated"

    @pytest.mark.parametrize("tool_name", DESTRUCTIVE_TOOLS)
    async def test_missing_actor_is_blocked(
        self,
        tool_name: str,
        fake_app_state: AppState,
    ) -> None:
        handlers = build_handler_map()
        handler = handlers[tool_name]
        args = _baseline_args(tool_name) | {"confirm": True, "reason": "x"}
        result = await handler(
            app_state=fake_app_state,
            arguments=args,
            actor=None,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "guardrail_violated"

    @pytest.mark.parametrize("tool_name", DESTRUCTIVE_TOOLS)
    async def test_string_confirm_is_rejected(
        self,
        tool_name: str,
        fake_app_state: AppState,
        actor: AgentIdentity,
    ) -> None:
        """``confirm: "true"`` (string) must fail; truthy-bypass would be a bug."""
        handlers = build_handler_map()
        handler = handlers[tool_name]
        args = _baseline_args(tool_name) | {
            "confirm": "true",  # intentionally a string, not a bool
            "reason": "x",
        }
        result = await handler(
            app_state=fake_app_state,
            arguments=args,
            actor=actor,
        )
        body = json.loads(result)
        assert body["status"] == "error"
        assert body["domain_code"] == "guardrail_violated", (
            f"{tool_name}: confirm='true' (string) should fail "
            f"but returned {body.get('domain_code')!r}"
        )


_ID_KEY_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("synthorg_agents_", "agent_name"),
    ("synthorg_approvals_", "approval_id"),
    ("synthorg_tasks_", "task_id"),
    ("synthorg_workflows_", "workflow_id"),
    ("synthorg_subworkflows_", "subworkflow_id"),
    ("synthorg_workflow_executions_", "execution_id"),
    ("synthorg_messages_", "message_id"),
    ("synthorg_meetings_", "meeting_id"),
    ("synthorg_connections_", "connection_id"),
    ("synthorg_webhooks_", "webhook_id"),
    ("synthorg_memory_", "checkpoint_id"),
    ("synthorg_mcp_catalog_", "server_id"),
    ("synthorg_oauth_", "provider"),
    ("synthorg_clients_", "client_id"),
    ("synthorg_artifacts_", "artifact_id"),
    ("synthorg_settings_", "key"),
    ("synthorg_backup_", "backup_id"),
    ("synthorg_users_", "user_id"),
    ("synthorg_projects_", "project_id"),
    ("synthorg_template_packs_", "pack_id"),
    ("synthorg_departments_", "department_id"),
    ("synthorg_teams_", "team_id"),
)


def _guess_id_key(tool_name: str) -> str | None:
    """Best-effort mapping of destructive-op tool name to its id argument."""
    for prefix, key in _ID_KEY_BY_PREFIX:
        if tool_name.startswith(prefix):
            return key
    return None
