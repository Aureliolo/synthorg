"""Unit tests for memory-domain destructive handlers.

Covers the success-path audit trail for ``cancel_fine_tune``,
``rollback_checkpoint``, and ``delete_checkpoint``: each must emit
:data:`MCP_DESTRUCTIVE_OP_EXECUTED` exactly once with the **resolved**
actor (not the caller-provided one, which may be ``None`` before
guardrail enrichment) and the supplied ``reason`` / ``target_id``.

The generic ``DESTRUCTIVE_TOOLS`` sweep in
``tests/unit/meta/mcp/test_all_handlers_wired.py`` already covers the
guardrail rejection branches; this file locks in the audit-event
invariant for the success path.
"""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import structlog.testing

from synthorg.core.agent import AgentIdentity
from synthorg.meta.mcp.handlers.memory import MEMORY_HANDLERS
from synthorg.observability.events.mcp import (
    MCP_DESTRUCTIVE_OP_EXECUTED,
    MCP_HANDLER_INVOKE_SUCCESS,
)
from tests.unit.meta.mcp.conftest import make_test_actor

pytestmark = pytest.mark.unit


@pytest.fixture
def actor() -> AgentIdentity:
    """Caller-authenticated actor passed to the handler."""
    return make_test_actor(name="cancel-caller")


def _fake_state_with_cancel(run_id: str = "run-cancelled") -> SimpleNamespace:
    """Wired state where ``cancel_fine_tune`` cancels an active run.

    The handler now guards ``MCP_DESTRUCTIVE_OP_EXECUTED`` on a
    non-``None`` ``target_id`` (no false audit entries for cancels
    issued when no run was active). Return a real ``run_id`` here so
    the destructive-op assertion in the happy-path test actually
    exercises the audit emission branch.
    """
    memory_service = AsyncMock()
    memory_service.cancel_fine_tune.return_value = run_id
    return SimpleNamespace(
        memory_service=memory_service,
        has_memory_service=True,
    )


def _fake_state_with_rollback(checkpoint_id: str) -> SimpleNamespace:
    cp = SimpleNamespace(
        model_dump=lambda mode="json": {
            "checkpoint_id": checkpoint_id,
            "status": "rolled_back",
        },
    )
    memory_service = AsyncMock()
    memory_service.rollback_checkpoint.return_value = cp
    return SimpleNamespace(
        memory_service=memory_service,
        has_memory_service=True,
    )


def _fake_state_with_delete(checkpoint_id: str) -> SimpleNamespace:
    """Wired-service app state that drives the happy-path delete audit.

    Previous iterations of this fixture used a bare-persistence state
    and let the test tolerate a ``not_supported`` response. That made
    the test non-deterministic: a regression that dropped the
    destructive-op emission on the wired service path could still pass
    because the test would quietly take the ``not_supported`` branch.
    Now we always mount a ``memory_service`` stub whose
    ``delete_checkpoint`` succeeds, so the handler must take the
    ``status == "ok"`` branch and emit ``MCP_DESTRUCTIVE_OP_EXECUTED``.
    """
    memory_service = AsyncMock()
    memory_service.delete_checkpoint.return_value = None
    return SimpleNamespace(
        memory_service=memory_service,
        has_memory_service=True,
    )


class TestCancelFineTuneDestructiveAudit:
    async def test_success_emits_destructive_op_executed(
        self,
        actor: AgentIdentity,
    ) -> None:
        state = _fake_state_with_cancel()
        handler = MEMORY_HANDLERS["synthorg_memory_cancel_fine_tune"]
        with structlog.testing.capture_logs() as events:
            raw = await handler(
                app_state=state,
                arguments={"reason": "operator-initiated abort", "confirm": True},
                actor=actor,
            )
        body = json.loads(raw)
        assert body["status"] == "ok"
        destructive = [
            e
            for e in events
            if e.get("event") == MCP_DESTRUCTIVE_OP_EXECUTED
            and e.get("tool_name") == "synthorg_memory_cancel_fine_tune"
        ]
        assert len(destructive) == 1, (
            "exactly one MCP_DESTRUCTIVE_OP_EXECUTED per successful call"
        )
        event = destructive[0]
        assert event["actor_agent_id"] == str(actor.id)
        assert event["reason"] == "operator-initiated abort"
        assert any(e.get("event") == MCP_HANDLER_INVOKE_SUCCESS for e in events), (
            "invoke-success must still fire alongside the destructive event"
        )


class TestRollbackCheckpointDestructiveAudit:
    async def test_success_emits_destructive_op_executed(
        self,
        actor: AgentIdentity,
    ) -> None:
        checkpoint_id = f"ckpt-{uuid4().hex}"
        state = _fake_state_with_rollback(checkpoint_id)
        handler = MEMORY_HANDLERS["synthorg_memory_rollback_checkpoint"]
        with structlog.testing.capture_logs() as events:
            raw = await handler(
                app_state=state,
                arguments={
                    "checkpoint_id": checkpoint_id,
                    "reason": "rolling back broken deploy",
                    "confirm": True,
                },
                actor=actor,
            )
        body = json.loads(raw)
        assert body["status"] == "ok"
        destructive = [
            e
            for e in events
            if e.get("event") == MCP_DESTRUCTIVE_OP_EXECUTED
            and e.get("tool_name") == "synthorg_memory_rollback_checkpoint"
        ]
        assert len(destructive) == 1
        event = destructive[0]
        assert event["actor_agent_id"] == str(actor.id)
        assert event["reason"] == "rolling back broken deploy"
        assert event["target_id"] == checkpoint_id


class TestDeleteCheckpointDestructiveAudit:
    async def test_success_emits_destructive_op_executed(
        self,
        actor: AgentIdentity,
    ) -> None:
        """Happy-path delete must always emit the destructive-op audit.

        Mirrors the cancel / rollback contracts: the wired
        ``memory_service`` stub returns success, so the handler takes
        the ``status == "ok"`` branch and must emit exactly one
        ``MCP_DESTRUCTIVE_OP_EXECUTED`` event with the resolved actor,
        reason, and target_id. Non-``ok`` responses are a regression
        and fail the test directly -- the previous conditional
        assertion let that regression pass silently.
        """
        checkpoint_id = f"ckpt-{uuid4().hex}"
        state = _fake_state_with_delete(checkpoint_id)
        handler = MEMORY_HANDLERS["synthorg_memory_delete_checkpoint"]
        with structlog.testing.capture_logs() as events:
            raw = await handler(
                app_state=state,
                arguments={
                    "checkpoint_id": checkpoint_id,
                    "reason": "checkpoint superseded",
                    "confirm": True,
                },
                actor=actor,
            )
        body: dict[str, Any] = json.loads(raw)
        assert body["status"] == "ok", (
            f"delete_checkpoint must succeed against a wired memory_service; "
            f"got status={body['status']!r}"
        )
        destructive = [
            e
            for e in events
            if e.get("event") == MCP_DESTRUCTIVE_OP_EXECUTED
            and e.get("tool_name") == "synthorg_memory_delete_checkpoint"
        ]
        assert len(destructive) == 1
        event = destructive[0]
        assert event["actor_agent_id"] == str(actor.id)
        assert event["reason"] == "checkpoint superseded"
        assert event["target_id"] == checkpoint_id
        state.memory_service.delete_checkpoint.assert_awaited_once()


def _fake_state_with_delete_entry(*, deleted: bool = True) -> SimpleNamespace:
    """Wired-service app state that drives the happy-path entry-delete audit."""
    memory_service = AsyncMock()
    memory_service.delete_memory_entry.return_value = deleted
    return SimpleNamespace(
        memory_service=memory_service,
        has_memory_service=True,
    )


class TestDeleteMemoryEntryDestructiveAudit:
    async def test_success_emits_destructive_op_executed(
        self,
        actor: AgentIdentity,
    ) -> None:
        """Happy-path memory-entry delete emits the destructive audit event."""
        agent_id = f"agent-{uuid4().hex}"
        memory_id = f"mem-{uuid4().hex}"
        state = _fake_state_with_delete_entry(deleted=True)
        handler = MEMORY_HANDLERS["synthorg_memory_delete_entry"]
        with structlog.testing.capture_logs() as events:
            raw = await handler(
                app_state=state,
                arguments={
                    "agent_id": agent_id,
                    "memory_id": memory_id,
                    "reason": "operator gdpr request",
                    "confirm": True,
                },
                actor=actor,
            )
        body: dict[str, Any] = json.loads(raw)
        assert body["status"] == "ok"
        destructive = [
            e
            for e in events
            if e.get("event") == MCP_DESTRUCTIVE_OP_EXECUTED
            and e.get("tool_name") == "synthorg_memory_delete_entry"
        ]
        assert len(destructive) == 1
        event = destructive[0]
        assert event["actor_agent_id"] == str(actor.id)
        assert event["reason"] == "operator gdpr request"
        assert event["target_id"] == memory_id
        assert event["agent_id"] == agent_id
        state.memory_service.delete_memory_entry.assert_awaited_once()

    async def test_not_found_returns_not_found_envelope(
        self,
        actor: AgentIdentity,
    ) -> None:
        """When the backend returns False, the handler emits a not_found envelope."""
        state = _fake_state_with_delete_entry(deleted=False)
        handler = MEMORY_HANDLERS["synthorg_memory_delete_entry"]
        raw = await handler(
            app_state=state,
            arguments={
                "agent_id": "agent-x",
                "memory_id": "missing-mem",
                "reason": "cleanup",
                "confirm": True,
            },
            actor=actor,
        )
        body: dict[str, Any] = json.loads(raw)
        assert body["status"] == "error"
        assert body["domain_code"] == "not_found"

    async def test_missing_confirm_rejects_with_guardrail(
        self,
        actor: AgentIdentity,
    ) -> None:
        """Missing ``confirm=True`` is rejected before any service call."""
        state = _fake_state_with_delete_entry(deleted=True)
        handler = MEMORY_HANDLERS["synthorg_memory_delete_entry"]
        raw = await handler(
            app_state=state,
            arguments={
                "agent_id": "agent-x",
                "memory_id": "mem-y",
                "reason": "cleanup",
                # ``confirm`` intentionally omitted
            },
            actor=actor,
        )
        body: dict[str, Any] = json.loads(raw)
        assert body["status"] == "error"
        state.memory_service.delete_memory_entry.assert_not_awaited()
