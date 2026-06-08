"""META-MCP acceptance sweep for the full 242-tool MCP surface.

The unit sweep in ``tests/unit/meta/mcp/test_all_handlers_wired.py``
already asserts parity between the registry and the handler map, and
guardrail rejection shape for the destructive subset.  This integration
sweep layers the acceptance criteria on top:

1. **Zero ``MCP_HANDLER_SERVICE_FALLBACK`` emissions** for any read or
   invoke path. The legacy ``service_fallback()`` helper stays in
   ``common.py`` for future surgical use, but it must have zero call
   sites in the handler tree after META-MCP-2.
2. **Typed capability events are the only ``not_supported`` sources**.
   Every ``not_supported`` wire envelope must be paired with either
   - ``MCP_HANDLER_CAPABILITY_GAP`` (INFO): handler is wired but the
     underlying primitive does not yet expose the required method, or
   - ``MCP_HANDLER_NOT_IMPLEMENTED`` (WARNING): the active backend
     cannot support the operation at all. META-MCP-4 introduced
     :class:`MemoryBackendUnsupportedError` + :func:`not_supported` so the
     memory fine-tune handlers emit this variant when the wired
     :class:`MemoryService` refuses a lifecycle call.

   Both events carry the same ``domain_code="not_supported"`` wire
   envelope and must ship a matching ``tool_name`` for telemetry.
3. **Every tool returns a well-formed envelope** -- ``status`` is
   always ``"ok"`` or ``"error"``, never ``"not_implemented"``.
"""

import json
from collections import Counter
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing
from typeguard import suppress_type_checks

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.core.agent import AgentIdentity
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import (
    CollaborationCalibration,
    CollaborationScoreResult,
)
from synthorg.meta.mcp.domains import build_full_registry
from synthorg.meta.mcp.handlers import build_handler_map
from synthorg.observability.events.mcp import (
    MCP_HANDLER_CAPABILITY_GAP,
    MCP_HANDLER_NOT_IMPLEMENTED,
    MCP_HANDLER_SERVICE_FALLBACK,
)
from synthorg.security.autonomy.models import AutonomyUpdateResult
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.integration


def _sync_dumped(data: dict[str, Any]) -> MagicMock:
    mock = MagicMock()
    mock.model_dump = MagicMock(return_value=data)
    for k, v in data.items():
        setattr(mock, k, v)
    return mock


def _mkversion_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.list_versions.return_value = ()
    repo.count_versions.return_value = 0
    repo.get_version.return_value = None
    return repo


@pytest.fixture
def fake_app_state() -> Any:
    """Wide-blast ``AppState`` stub covering every primitive the handlers probe.

    Returns a live ``AppState`` so handlers can call ``app_state.slice(...)``
    and the ``*_of`` accessors; sub-mocks are wired through ``make_app_state``
    keyed by the legacy attribute names.
    """
    from synthorg.persistence.fine_tune_protocol import (
        FineTuneCheckpointRepository,
        FineTuneRunRepository,
    )
    from synthorg.persistence.protocol import PersistenceBackend
    from synthorg.persistence.version_protocol import VersionRepository
    from tests._shared import make_app_state

    dummy_task = _sync_dumped({"id": "task-1", "title": "x"})

    defrepo = AsyncMock()
    defrepo.query.return_value = ()
    defrepo.get.return_value = None
    defrepo.delete.return_value = False

    fine_tune_checkpoints = AsyncMock(spec=FineTuneCheckpointRepository)
    fine_tune_checkpoints.list_items_page.return_value = ((), 0)
    fine_tune_checkpoints.get.return_value = None

    persistence = MagicMock(spec=PersistenceBackend)
    persistence.workflow_definitions = defrepo
    persistence.workflow_versions = AsyncMock(spec=VersionRepository)
    persistence.budget_config_versions = _mkversion_repo()
    persistence.fine_tune_checkpoints = fine_tune_checkpoints
    persistence.fine_tune_runs = AsyncMock(spec=FineTuneRunRepository)

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
    # META-MCP-3 write facades return Pydantic shapes; AsyncMock returns
    # would JSON-serialise as coroutines and crash the envelope.
    dummy_identity = make_test_actor(name="alpha")
    registry.apply_identity_update.return_value = dummy_identity
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
        {"currency": DEFAULT_CURRENCY},
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


@pytest.fixture
def actor() -> AgentIdentity:
    return make_test_actor()


_BLAST_ARGS: dict[str, Any] = {
    "agent_name": "alpha",
    "agent_id": "agent-1",
    "approval_id": "approval-1",
    "task_id": "task-1",
    "workflow_id": "wf-1",
    "subworkflow_id": "sw-1",
    "execution_id": "ex-1",
    "checkpoint_id": "cp-1",
    "version": "1.0.0",
    "version_num": 1,
    "revision": 1,
    "title": "x",
    "description": "y",
    "action_type": "deploy",
    "risk_level": "medium",
    "confirm": True,
    "reason": "test reason for guardrail",
    "name": "alpha",
    "role": "engineer",
    "department": "Engineering",
    "session_id": "sess-1",
    "level": "full",
    "updates": {},
    "target_status": "in_progress",
    "steps": [],
    "status": "pending",
    # META-MCP-3 write-handler inputs.  ``identity`` and ``definition``
    # need to satisfy the live Pydantic validators; the values here are
    # minimal-but-valid so the handlers fall through to the service mocks
    # rather than failing the validation step.
    "identity": {},  # invalid -> handler returns ``invalid_argument`` (ok)
    "definition": {},  # invalid -> handler returns ``invalid_argument`` (ok)
    "task_data": {},  # invalid -> handler returns ``invalid_argument`` (ok)
    "project": "default",
    "context": {},
}


class TestNoServiceFallbackEvents:
    """Acceptance gate: zero MCP_HANDLER_SERVICE_FALLBACK events after META-MCP-2."""

    async def test_invoking_every_tool_emits_no_service_fallback_event(
        self,
        fake_app_state: Any,
        actor: AgentIdentity,
    ) -> None:
        handlers = build_handler_map()
        with structlog.testing.capture_logs() as events:
            for handler in handlers.values():
                await handler(
                    app_state=fake_app_state,
                    arguments=dict(_BLAST_ARGS),
                    actor=actor,
                )
        fallback_events = [
            e for e in events if e.get("event") == MCP_HANDLER_SERVICE_FALLBACK
        ]
        assert not fallback_events, (
            f"MCP_HANDLER_SERVICE_FALLBACK must be unused after META-MCP-2, "
            f"but {len(fallback_events)} emissions fired: "
            f"{[e.get('tool_name') for e in fallback_events]}"
        )

    async def test_capability_gap_is_the_not_supported_source(
        self,
        fake_app_state: Any,
        actor: AgentIdentity,
    ) -> None:
        """Tools returning ``not_supported`` must emit CAPABILITY_GAP (INFO).

        The stubbed ``fake_app_state`` omits several optional primitives
        (``has_settings_service=False``, ``persistence`` accessors that
        never expose evaluation-config tables, etc.), so at least a few
        tools should hit the capability-gap path.  This test pins the
        invariant: every ``not_supported`` wire envelope must be paired
        with a ``MCP_HANDLER_CAPABILITY_GAP`` event -- never a bare
        ``MCP_HANDLER_SERVICE_FALLBACK`` or silent ``err(..., not_supported)``.
        """
        handlers = build_handler_map()
        with structlog.testing.capture_logs() as events:
            not_supported_tools: list[str] = []
            for tool_name, handler in handlers.items():
                raw = await handler(
                    app_state=fake_app_state,
                    arguments=dict(_BLAST_ARGS),
                    actor=actor,
                )
                body = json.loads(raw)
                if body.get("domain_code") == "not_supported":
                    not_supported_tools.append(tool_name)
        # Either event is a valid source for a ``not_supported`` envelope:
        # - CAPABILITY_GAP: handler is wired but the primitive does not
        #   expose the method yet (live-handler gap).
        # - NOT_IMPLEMENTED: the active persistence backend cannot
        #   support the operation at all (META-MCP-4 introduced
        #   ``MemoryBackendUnsupportedError`` + ``not_supported()`` for
        #   memory fine-tune sites).
        gap_events = [
            e
            for e in events
            if e.get("event")
            in {MCP_HANDLER_CAPABILITY_GAP, MCP_HANDLER_NOT_IMPLEMENTED}
        ]
        # The pairing invariant is unconditional: an ``all-supported``
        # run (``not_supported_tools == []``) must also produce zero
        # gap events, because an orphan CAPABILITY_GAP /
        # NOT_IMPLEMENTED event without a matching ``not_supported``
        # envelope indicates a handler is emitting the audit signal
        # on the wrong branch. Gating the pairing check on
        # ``if not_supported_tools`` silently tolerates that case.
        assert all("tool_name" in e for e in gap_events), (
            "every capability event must identify the tool that triggered it"
        )
        # Use Counter, not set, so a tool that emits the envelope
        # once but emits CAPABILITY_GAP / NOT_IMPLEMENTED twice
        # fails the invariant. The test is about strict 1:1
        # envelope-to-event pairing.
        gap_tool_counts = Counter(e["tool_name"] for e in gap_events)
        not_supported_counts = Counter(not_supported_tools)
        assert gap_tool_counts == not_supported_counts, (
            f"1:1 mismatch between not_supported envelopes and "
            f"capability events. "
            f"Envelope-but-no-event: "
            f"{sorted(set(not_supported_counts) - set(gap_tool_counts))}. "
            f"Event-but-no-envelope: "
            f"{sorted(set(gap_tool_counts) - set(not_supported_counts))}."
        )

    @pytest.mark.parametrize(
        "tool_name",
        [
            "synthorg_memory_get_fine_tune_status",
            "synthorg_memory_list_runs",
            "synthorg_memory_get_active_embedder",
        ],
    )
    async def test_backend_unsupported_error_routes_to_not_supported(
        self,
        fake_app_state: Any,
        actor: AgentIdentity,
        tool_name: str,
    ) -> None:
        """A :class:`MemoryBackendUnsupportedError` lands as not_supported.

        Injects a ``memory_service`` stub whose methods raise
        ``MemoryBackendUnsupportedError`` so the handler's catch + forward
        to ``not_supported()`` is exercised end to end. The default
        ``fake_app_state`` fixture wires a working memory_service, so
        this branch is never hit by the generic blast; this test
        pins the behaviour explicitly.
        """
        from synthorg.memory.fine_tune_plan import MemoryBackendUnsupportedError

        unsupported_reason = "fine-tune repositories not available on active backend"

        class _UnsupportedMemoryService:
            async def get_fine_tune_status(
                self,
                run_id: Any = None,
            ) -> None:
                raise MemoryBackendUnsupportedError(unsupported_reason)

            async def list_runs(
                self,
                *,
                offset: int,
                limit: int,
            ) -> None:
                raise MemoryBackendUnsupportedError(unsupported_reason)

            async def get_active_embedder(self) -> None:
                raise MemoryBackendUnsupportedError(unsupported_reason)

        from synthorg.memory.state import MemoryStateSlice

        fake_app_state.wire(MemoryStateSlice, service=_UnsupportedMemoryService())

        handlers = build_handler_map()
        handler = handlers[tool_name]
        # The memory-service accessor is annotated -> MemoryService, so the
        # WARN-level runtime checker rejects this lightweight stub before its
        # MemoryBackendUnsupportedError can reach the handler's catch. Suppress
        # the structural check so the genuine error-routing path is exercised.
        with structlog.testing.capture_logs() as events, suppress_type_checks():
            raw = await handler(
                app_state=fake_app_state,
                arguments=dict(_BLAST_ARGS),
                actor=actor,
            )
        body = json.loads(raw)
        assert body["domain_code"] == "not_supported", (
            f"{tool_name} should return not_supported when BackendUnsupported "
            f"fires, got {body!r}"
        )
        not_implemented = [
            e
            for e in events
            if e.get("event") == MCP_HANDLER_NOT_IMPLEMENTED
            and e.get("tool_name") == tool_name
        ]
        assert not_implemented, (
            f"{tool_name} routed via not_supported() but no "
            f"MCP_HANDLER_NOT_IMPLEMENTED event fired"
        )

    async def test_every_tool_returns_well_formed_envelope(
        self,
        fake_app_state: Any,
        actor: AgentIdentity,
    ) -> None:
        """Each tool returns ``status`` in {ok, error}, with mandatory keys."""
        registry = build_full_registry()
        handlers = build_handler_map()
        for tool_name in registry.get_names():
            handler = handlers[tool_name]
            raw = await handler(
                app_state=fake_app_state,
                arguments=dict(_BLAST_ARGS),
                actor=actor,
            )
            body = json.loads(raw)
            assert body["status"] in {"ok", "error"}, (
                f"{tool_name} returned unexpected status {body.get('status')!r}"
            )
            if body["status"] == "error":
                assert "message" in body, f"{tool_name} error lacks message"


class TestToolSurfaceCount:
    """Pin the tool count at 242 to catch accidental add/remove regressions."""

    def test_total_tool_count_is_242(self) -> None:
        registry = build_full_registry()
        assert registry.tool_count == 242

    def test_no_orphan_handlers(self) -> None:
        registry = build_full_registry()
        handlers = build_handler_map()
        orphans = set(handlers.keys()) - set(registry.get_names())
        assert not orphans
        missing = set(registry.get_names()) - set(handlers.keys())
        assert not missing
