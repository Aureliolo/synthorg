# module-kind: tests
"""Conformance tests for the ``ExecutionResultView`` protocol.

``budget.coordination_collector`` stays engine-free by annotating against
``ExecutionResultView`` instead of the concrete
``engine.loop_protocol.ExecutionResult``. That decoupling only holds if
``ExecutionResult`` actually satisfies the protocol, so this pins the
contract directly: a rename or removal of ``ExecutionResult.turns`` fails
here immediately rather than surfacing later as an ``AttributeError`` deep
in the collector.
"""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.execution.view import ExecutionResultView


def _execution_result() -> ExecutionResult:
    """Build a real ExecutionResult with a real AgentContext."""
    identity = AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-large-001"),
        hiring_date=date(2026, 1, 1),
    )
    return ExecutionResult(
        context=AgentContext.from_identity(identity),
        termination_reason=TerminationReason.COMPLETED,
    )


@pytest.mark.unit
def test_execution_result_satisfies_view() -> None:
    """ExecutionResult structurally satisfies ExecutionResultView."""
    result = _execution_result()
    assert isinstance(result, ExecutionResultView)


@pytest.mark.unit
def test_object_without_turns_is_not_a_view() -> None:
    """The runtime check rejects an object lacking ``turns``."""

    class _NoTurns:
        pass

    assert not isinstance(_NoTurns(), ExecutionResultView)
