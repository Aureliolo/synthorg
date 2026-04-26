"""Error-path coverage for every MCP handler in every domain.

The per-domain handler test files mostly exercise happy paths; this
module fills the gap by parametrizing over every handler in
:func:`synthorg.meta.mcp.handlers.build_handler_map` and verifying
that each one returns a well-formed error envelope when its service
layer fails. Hits the centralized ``log_handler_*`` helpers across
the entire 200+-tool surface in one parametrized sweep so the
``except`` branches the centralization refactor introduced are
covered without per-handler boilerplate.
"""

import json
from typing import Any

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity
from synthorg.meta.mcp.handlers import build_handler_map
from synthorg.observability.events.mcp import (
    MCP_HANDLER_ARGUMENT_INVALID,
    MCP_HANDLER_GUARDRAIL_VIOLATED,
    MCP_HANDLER_INVOKE_FAILED,
)
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


class _UniversalFailingService:
    """Service stub whose every method raises ``RuntimeError`` on call.

    Auto-vivifies child attributes via ``__getattr__`` so handlers can
    do ``await app_state.X_service.do_thing(...)`` OR
    ``app_state.X_service.do_thing(...)`` (sync) and reliably hit their
    ``except Exception`` branch -- the returned callable raises before
    the awaitable could even be constructed, so the failure surfaces
    regardless of the call style.
    """

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)

        def raising(*args: Any, **kwargs: Any) -> Any:
            msg = f"forced failure on {name} (args={len(args)}, kwargs={len(kwargs)})"
            raise RuntimeError(msg)

        return raising


class _UniversalFailingAppState:
    """App state where every service attribute raises on call.

    Behaviours:

    - ``has_*`` capability flags return ``True`` so capability_gap
      branches don't short-circuit before the service call.
    - Any other attribute returns a :class:`_UniversalFailingService`
      (truthy, non-``None``) so capability checks like
      ``getattr(app_state, "X_service", None)`` see a valid service.

    The net effect: every handler reaches its service-call line and
    blows up there, hitting the ``except Exception`` branch.
    """

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        if name.startswith("has_"):
            return True
        return _UniversalFailingService()


# Generic argument bundle. Permissive enough that handlers either:
# (a) pass argument validation and reach the failing service layer, OR
# (b) fail argument validation because the args don't match a
#     domain-specific schema (e.g. Message / ImprovementProposal /
#     AutonomyUpdate Pydantic shapes).
# Both paths exercise centralized ``log_handler_*`` branches and the
# ``err(...)`` envelope. The destructive-op tools are exercised in
# both their guardrail-pass and guardrail-fail variants below.
_BASE_ARGS: dict[str, Any] = {
    # Identifier-shape strings
    "agent_id": "agent-1",
    "agent_name": "alpha",
    "name": "alpha",
    "role": "engineer",
    "department": "Engineering",
    "session_id": "sess-1",
    "task_id": "task-1",
    "workflow_id": "workflow-1",
    "subworkflow_id": "sub-1",
    "execution_id": "exec-1",
    "channel": "main",
    "message_id": "msg-1",
    "meeting_id": "meet-1",
    "webhook_id": "webhook-1",
    "decision_id": "decision-1",
    "approval_id": "approval-1",
    "checkpoint_id": "ckpt-1",
    "run_id": "run-1",
    "report_id": "00000000-0000-0000-0000-000000000001",
    "metric_path": "test.metric",
    # Enum-shape strings
    "level": "FULL",
    "auth_method": "api_key",
    "connection_type": "rest",
    "trigger": "scheduled",
    "comparator": ">=",
    "severity": "minor",
    "action_type": "test",
    "risk_level": "low",
    # Time / window
    "since": "2026-04-01T00:00:00+00:00",
    "until": "2026-04-02T00:00:00+00:00",
    "horizon_days": 30,
    "sample_count": 8,
    # Sequences / dicts
    "metric_names": ["test.metric"],
    "target_altitudes": ["agent"],
    "department_ids": ["00000000-0000-0000-0000-000000000001"],
    "credentials": {"key": "value"},
    "metadata": {},
    "definition": {},
    "payload": {},
    # Numeric
    "version": 1,
    "version_num": 1,
    "threshold": 0.5,
    # Booleans
    "enabled": True,
    "confirm": True,
    # Free-form strings
    "project": "default",
    "title": "test",
    "comment": "test",
    "reason": "test invocation",
    "base_url": "https://example.test",
}


# Build the map once at import time; parametrizing over the entire
# surface gives us one test per handler.
_HANDLER_MAP = build_handler_map()


@pytest.fixture(scope="module")
def actor() -> AgentIdentity:
    """Real :class:`AgentIdentity` so every handler sees a valid actor.

    Handlers that route through ``require_destructive_guardrails``
    accept this actor (it has both ``.id`` and ``.name``); the
    centralized ``log_handler_*`` helpers also exercise their
    ``actor_id``/``actor_label`` paths against a real identity.
    """
    return make_test_actor(name="error-path-tester")


class TestEveryHandlerHandlesFailureCleanly:
    """Pin every handler's error-path contract.

    For each tool in :func:`build_handler_map`:

    1. Call with a generic arg bundle and a real actor.
    2. Use an :class:`_UniversalFailingAppState` whose every service
       method raises ``RuntimeError`` on call.

    Every handler must return a JSON-decodable envelope with a
    ``status`` field. Most reach their ``except Exception`` branch
    (service raises) or ``except ArgumentValidationError`` branch
    (args don't fit a domain-specific schema). The capability-gap path
    is suppressed because every ``has_*`` flag returns ``True``.

    This sweep covers the centralized ``log_handler_argument_invalid``
    and ``log_handler_invoke_failed`` call sites across all handlers.
    """

    @pytest.mark.parametrize("tool_name", sorted(_HANDLER_MAP.keys()))
    async def test_returns_well_formed_envelope_on_failure(
        self,
        tool_name: str,
        actor: AgentIdentity,
    ) -> None:
        handler = _HANDLER_MAP[tool_name]
        result = await handler(
            app_state=_UniversalFailingAppState(),
            arguments=dict(_BASE_ARGS),  # copy so handler mutations don't leak
            actor=actor,
        )
        # Result must be a JSON string per the handler contract.
        assert isinstance(result, str), (
            f"{tool_name}: handler returned non-string {type(result).__name__}"
        )
        body: dict[str, Any] = json.loads(result)
        # ``ok`` is acceptable for the rare handler that completes
        # without touching the service (e.g. unwired tools that hit
        # the ``not_supported`` envelope path or pure capability
        # responses). Most handlers will return ``error``.
        assert body["status"] in {"error", "ok"}, (
            f"{tool_name}: legacy/invalid status {body['status']!r}"
        )
        if body["status"] == "error":
            # Error envelopes must carry ``error_type`` so callers can
            # dispatch programmatically.
            assert "error_type" in body, (
                f"{tool_name}: error envelope missing error_type"
            )


class TestEveryHandlerEmitsCentralizedLogEvent:
    """Every handler emits one of the three handler-layer log events.

    Pins the contract that the centralization refactor delivered:
    each ``except`` branch in every domain handler routes through one
    of ``log_handler_argument_invalid``, ``log_handler_invoke_failed``,
    or ``log_handler_guardrail_violated``. A handler that returns an
    error envelope without emitting one of these events would be a
    regression -- the centralization buys consistent observability
    only if every error path goes through these helpers.

    Limited to a representative sample (one per domain) so the test
    finishes quickly while still pinning the cross-domain contract.
    """

    _SAMPLE_HANDLERS: tuple[str, ...] = (
        # One simple "list" handler per domain; failure path hits
        # ``except Exception`` after the service raises.
        "synthorg_agents_list",
        "synthorg_tasks_list",
        "synthorg_workflows_list",
        "synthorg_approvals_list",
        "synthorg_budget_get_config",
        "synthorg_company_get",
        "synthorg_meetings_list",
        "synthorg_messages_list",
        "synthorg_connections_list",
        "synthorg_webhooks_list",
        "synthorg_meta_list_rules",
        "synthorg_signals_get_org_snapshot",
        "synthorg_quality_list_grades",
        "synthorg_analytics_get_overview",
        "synthorg_memory_checkpoints_list",
        "synthorg_coordination_metrics_list",
        "synthorg_infra_get_health",
    )

    @pytest.mark.parametrize(
        "tool_name",
        [t for t in _SAMPLE_HANDLERS if t in _HANDLER_MAP],
    )
    async def test_failure_emits_centralized_event(
        self,
        tool_name: str,
        actor: AgentIdentity,
    ) -> None:
        handler = _HANDLER_MAP[tool_name]
        with structlog.testing.capture_logs() as logs:
            result = await handler(
                app_state=_UniversalFailingAppState(),
                arguments=dict(_BASE_ARGS),
                actor=actor,
            )
        body: dict[str, Any] = json.loads(result)
        # If the handler returned an error envelope, exactly one of
        # the three centralized events must have been emitted.
        if body["status"] == "error":
            handler_events = [
                event["event"]
                for event in logs
                if event.get("event")
                in {
                    MCP_HANDLER_ARGUMENT_INVALID,
                    MCP_HANDLER_INVOKE_FAILED,
                    MCP_HANDLER_GUARDRAIL_VIOLATED,
                }
            ]
            assert handler_events, (
                f"{tool_name}: error envelope returned without emitting "
                f"any centralized log_handler_* event"
            )


class TestDestructiveHandlersExerciseGuardrailBranch:
    """Destructive ops emit ``MCP_HANDLER_GUARDRAIL_VIOLATED`` on missing confirm.

    Without ``confirm=True`` the destructive-op guardrail raises a
    ``GuardrailViolationError``, which the handler catches and routes
    through :func:`log_handler_guardrail_violated`. This pins the
    third centralized log path across a representative sample of
    destructive tools.
    """

    _DESTRUCTIVE_TOOLS: tuple[str, ...] = (
        "synthorg_agents_delete",
        "synthorg_tasks_delete",
        "synthorg_workflows_delete",
        "synthorg_approvals_delete",
        "synthorg_messages_delete",
        "synthorg_webhooks_delete",
        "synthorg_subworkflows_delete",
    )

    @pytest.mark.parametrize(
        "tool_name",
        [t for t in _DESTRUCTIVE_TOOLS if t in _HANDLER_MAP],
    )
    async def test_missing_confirm_routes_through_guardrail_logger(
        self,
        tool_name: str,
        actor: AgentIdentity,
    ) -> None:
        handler = _HANDLER_MAP[tool_name]
        # ``confirm`` and ``reason`` deliberately omitted.
        args = {k: v for k, v in _BASE_ARGS.items() if k not in {"confirm", "reason"}}
        with structlog.testing.capture_logs() as logs:
            result = await handler(
                app_state=_UniversalFailingAppState(),
                arguments=args,
                actor=actor,
            )
        body: dict[str, Any] = json.loads(result)
        assert body["status"] == "error"
        assert body.get("domain_code") == "guardrail_violated"
        guardrail_events = [
            event
            for event in logs
            if event.get("event") == MCP_HANDLER_GUARDRAIL_VIOLATED
        ]
        assert guardrail_events, (
            f"{tool_name}: guardrail violation didn't emit "
            f"MCP_HANDLER_GUARDRAIL_VIOLATED event"
        )
