"""Structural view of an execution result for non-engine consumers.

``ExecutionResultView`` is the minimal read surface a consumer needs to
inspect the per-turn records of a completed run, without depending on the
concrete ``engine.loop_protocol.ExecutionResult`` (which carries an
``AgentContext`` field and is therefore welded to the engine package).

Subsystems such as ``budget.coordination_collector`` annotate against
this protocol so they stay engine-free and cold-importable;
``ExecutionResult`` satisfies it structurally (it exposes ``turns``).
"""

from typing import Protocol, runtime_checkable

from synthorg.execution.turn import TurnRecord


@runtime_checkable
class ExecutionResultView(Protocol):
    """Read-only structural view of an execution result.

    Attributes:
        turns: Per-turn metadata records produced by the run.
    """

    @property
    def turns(self) -> tuple[TurnRecord, ...]:
        """Per-turn metadata records produced by the run."""
        ...
