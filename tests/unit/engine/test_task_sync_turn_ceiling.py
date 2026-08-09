"""A park nobody can answer is a quieter deadlock than a failure.

The question a spent turn budget raises has no author: no tool call escalated
it, so nothing else would create the approval or the parked context the resume
router needs. Both halves are written here, and if either cannot be, the run
does not park at all.
"""

from datetime import date
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from synthorg.approval.enums import ApprovalSource
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.approval import ApprovalItem
from synthorg.core.persistence_errors import PersistenceError
from synthorg.engine.approval_gate import ApprovalGate
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.loop_turn_budget import TURN_CEILING_METADATA_KEY
from synthorg.engine.task_sync_turn_ceiling import (
    TURN_CEILING_ACTION_TYPE,
    arm_turn_ceiling_park,
)
from tests._shared import mock_of

pytestmark = pytest.mark.unit


_AGENT_ID = "agent-ceiling"
_TASK_ID = "task-ceiling"


def _ctx() -> AgentContext:
    identity = AgentIdentity(
        name="Ceiling Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )
    return AgentContext.from_identity(identity, max_turns=20)


def _parked() -> ExecutionResult:
    return ExecutionResult(
        context=_ctx(),
        termination_reason=TerminationReason.PARKED,
        metadata={TURN_CEILING_METADATA_KEY: True},
    )


def _store(add: AsyncMock | None = None) -> ApprovalStoreProtocol:
    writes = add if add is not None else AsyncMock(return_value=None)
    store: ApprovalStoreProtocol = mock_of[ApprovalStoreProtocol](add=writes)
    return store


def _gate(park: AsyncMock | None = None) -> ApprovalGate:
    parks = park if park is not None else AsyncMock(return_value=None)
    gate: ApprovalGate = mock_of[ApprovalGate](park_context=parks)
    return gate


class TestArmTurnCeilingPark:
    async def test_a_park_that_is_not_a_ceiling_is_left_alone(self) -> None:
        result = ExecutionResult(
            context=_ctx(),
            termination_reason=TerminationReason.PARKED,
            metadata={"clarification": True},
        )
        store = _store()

        armed = await arm_turn_ceiling_park(
            result,
            agent_id=_AGENT_ID,
            task_id=_TASK_ID,
            approval_store=store,
            approval_gate=_gate(),
        )

        assert armed is result
        cast(Mock, store).add.assert_not_awaited()

    async def test_the_approval_routes_to_the_resume_path(self) -> None:
        """Any other source falls through to the review gate and strands it."""
        store = _store()

        armed = await arm_turn_ceiling_park(
            _parked(),
            agent_id=_AGENT_ID,
            task_id=_TASK_ID,
            approval_store=store,
            approval_gate=_gate(),
        )

        assert armed.termination_reason is TerminationReason.PARKED
        item = cast(Mock, store).add.await_args.args[0]
        assert isinstance(item, ApprovalItem)
        assert item.source is ApprovalSource.PARKED_CONTEXT
        assert item.task_id == _TASK_ID

    async def test_the_action_type_is_its_own(self) -> None:
        """An autonomy grant for a review must not also buy turn budget."""
        store = _store()

        await arm_turn_ceiling_park(
            _parked(),
            agent_id=_AGENT_ID,
            task_id=_TASK_ID,
            approval_store=store,
            approval_gate=_gate(),
        )

        raised = cast(Mock, store).add.await_args.args[0]
        assert raised.action_type == TURN_CEILING_ACTION_TYPE

    async def test_the_context_is_parked_under_the_same_approval(self) -> None:
        gate = _gate()

        await arm_turn_ceiling_park(
            _parked(),
            agent_id=_AGENT_ID,
            task_id=_TASK_ID,
            approval_store=_store(),
            approval_gate=gate,
        )

        kwargs = cast(Mock, gate).park_context.await_args.kwargs
        assert kwargs["task_id"] == _TASK_ID
        assert kwargs["escalation"].action_type == TURN_CEILING_ACTION_TYPE

    async def test_no_store_ends_the_run_instead(self) -> None:
        armed = await arm_turn_ceiling_park(
            _parked(),
            agent_id=_AGENT_ID,
            task_id=_TASK_ID,
            approval_store=None,
            approval_gate=_gate(),
        )

        assert armed.termination_reason is TerminationReason.MAX_TURNS
        assert TURN_CEILING_METADATA_KEY not in armed.metadata

    async def test_no_gate_ends_the_run_instead(self) -> None:
        armed = await arm_turn_ceiling_park(
            _parked(),
            agent_id=_AGENT_ID,
            task_id=_TASK_ID,
            approval_store=_store(),
            approval_gate=None,
        )

        assert armed.termination_reason is TerminationReason.MAX_TURNS
        assert TURN_CEILING_METADATA_KEY not in armed.metadata

    async def test_a_failed_write_ends_the_run_instead(self) -> None:
        """Half-armed is the deadlock: the marker goes with the park."""
        failing = AsyncMock(side_effect=PersistenceError("store down"))
        store = _store(failing)

        armed = await arm_turn_ceiling_park(
            _parked(),
            agent_id=_AGENT_ID,
            task_id=_TASK_ID,
            approval_store=store,
            approval_gate=_gate(),
        )

        assert armed.termination_reason is TerminationReason.MAX_TURNS
        assert TURN_CEILING_METADATA_KEY not in armed.metadata

    async def test_a_failed_park_ends_the_run_instead(self) -> None:
        refusing = AsyncMock(side_effect=PersistenceError("no repo"))
        store = _store()

        armed = await arm_turn_ceiling_park(
            _parked(),
            agent_id=_AGENT_ID,
            task_id=_TASK_ID,
            approval_store=store,
            approval_gate=_gate(refusing),
        )

        assert armed.termination_reason is TerminationReason.MAX_TURNS
        cast(Mock, store).add.assert_not_awaited()
