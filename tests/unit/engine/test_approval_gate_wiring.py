"""Tests for AgentEngine -> ApprovalGate timeout wiring (issue #1776).

Confirms that ``approval_interrupt_timeout_seconds`` (sourced from
``EngineBridgeConfig.approval_interrupt_timeout_seconds`` at the
construction site) actually reaches the ``ApprovalGate`` constructor
instead of being silently lost. The factory at
``engine/agent_engine_factories.py:_make_approval_gate`` had a
hardcoded 300s default before this wiring landed.
"""

from unittest.mock import AsyncMock

import pytest

from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.engine.agent_engine import AgentEngine

from .conftest import MockCompletionProvider


@pytest.mark.unit
class TestApprovalGateTimeoutWiring:
    """``approval_interrupt_timeout_seconds`` flows from engine to gate."""

    def test_explicit_timeout_propagates_to_gate(self) -> None:
        """A non-default timeout reaches the gate's interrupt-timeout field."""
        provider = MockCompletionProvider([])
        approval_store = AsyncMock(spec=ApprovalStoreProtocol)
        engine = AgentEngine(
            provider=provider,
            approval_store=approval_store,
            approval_interrupt_timeout_seconds=42.0,
        )
        assert engine._approval_gate is not None
        assert engine._approval_gate._interrupt_timeout_seconds == 42.0

    def test_omitted_timeout_falls_back_to_gate_default(self) -> None:
        """Without the kwarg the gate keeps its built-in 300s default.

        Back-compat: tests that don't yet thread the bridge config keep
        working unchanged. Production paths thread the resolved value.
        """
        provider = MockCompletionProvider([])
        approval_store = AsyncMock(spec=ApprovalStoreProtocol)
        engine = AgentEngine(
            provider=provider,
            approval_store=approval_store,
        )
        assert engine._approval_gate is not None
        assert engine._approval_gate._interrupt_timeout_seconds == 300.0

    def test_no_approval_store_yields_no_gate(self) -> None:
        """The factory short-circuits when no approval store is wired."""
        provider = MockCompletionProvider([])
        engine = AgentEngine(
            provider=provider,
            approval_interrupt_timeout_seconds=42.0,
        )
        assert engine._approval_gate is None
