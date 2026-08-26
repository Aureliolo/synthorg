"""A planning session must read as the org working while it runs.

Every surface answering "is the org working" reads the live agent-state rows,
written for the duration of an agent run and cleared when it ends. The
decomposition planning session builds its loop directly rather than through
``AgentEngine``, so it was the one agent run that claimed no row at all: a live
run planned for 54 minutes while the header read ``0 active | 12 idle``,
mission control read ``ACTIVE AGENTS 0`` and the pulse panel read "Nothing is
running", beside a Live Activity feed listing the planner's own API calls.

These assert the claim and the release, including the release after a session
that ended badly: a row left EXECUTING makes a finished agent look occupied for
the life of the process, which is the same defect pointing the other way.
"""

from typing import override

import pytest
from pydantic import JsonValue

from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_state import AgentRuntimeState, ExecutionStatus
from synthorg.engine.decomposition.agent_session import (
    AgentSessionDecompositionStrategy,
)
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.decomposition.protocol import DecompositionStrategy
from synthorg.engine.decomposition.strategy_deps import (
    AgentSessionDecompositionConfig,
    DecompositionStrategyDeps,
)
from synthorg.engine.errors import DecompositionError
from tests._shared import as_uuid
from tests._shared.scripted_provider import (
    ScriptedProvider,
    build_tool_call_response,
    make_e2e_identity,
    make_text_response,
)

pytestmark = pytest.mark.unit


class _RecordingStates:
    """An agent-state repository that remembers the sequence it was driven in.

    The order is the assertion: a claim that never lands leaves the surfaces
    blind, and a release that never lands leaves a finished agent reading busy
    for ever. Only the two writes the session makes carry behaviour; the rest
    of the protocol is implemented because typeguard checks the WHOLE shape at
    the boundary, not the members one caller happens to reach.
    """

    def __init__(self) -> None:
        self.saved: list[AgentRuntimeState] = []
        self.cleared: list[tuple[str, str]] = []

    async def save(self, entity: AgentRuntimeState, /) -> None:
        """Record a write that names no execution to guard on."""
        self.saved.append(entity)

    async def save_if_execution(
        self, entity: AgentRuntimeState, /, *, expected_execution_id: str
    ) -> bool:
        """Record a guarded write, split by what it is claiming.

        Both the claim and the release take this door (the claim guards
        against a sibling already holding the row), so they are told apart by
        the status written rather than by the method reached.

        Returns:
            ``True``, as a repository whose row still belongs to this run does.
        """
        if entity.status is ExecutionStatus.IDLE:
            self.cleared.append((entity.agent_id, expected_execution_id))
        else:
            self.saved.append(entity)
        return True

    async def get(self, entity_id: NotBlankStr, /) -> AgentRuntimeState | None:
        """Read one row.

        Returns:
            ``None``: nothing under test reads a row back.
        """
        del entity_id
        return None

    async def list_items(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[AgentRuntimeState, ...]:
        """List every row.

        Returns:
            The claims recorded so far.
        """
        del limit, offset
        return tuple(self.saved)

    async def get_active(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[AgentRuntimeState, ...]:
        """List the non-idle rows.

        Returns:
            The claims recorded so far.
        """
        del limit, offset
        return tuple(self.saved)

    async def delete(self, entity_id: NotBlankStr, /) -> bool:
        """Delete one row.

        Returns:
            ``False``: nothing under test deletes.
        """
        del entity_id
        return False


class _SentinelFallback(DecompositionStrategy):
    """A fallback these cases must never reach."""

    @override
    async def decompose(
        self, task: Task, context: DecompositionContext
    ) -> DecompositionPlan:
        """Fail loudly.

        Raises:
            AssertionError: Always.
        """
        del task, context
        msg = "the fallback must not stand in for the session under test"
        raise AssertionError(msg)

    @override
    def get_strategy_name(self) -> str:
        """Return the strategy name.

        Returns:
            The name.
        """
        return "sentinel"

    @override
    def plans_any_task(self) -> bool:
        """Answer whether it plans an arbitrary task.

        Returns:
            ``False``: it raises when invoked.
        """
        return False


def _task() -> Task:
    """Build the objective under decomposition.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid("obj-1"),
        title="Build a Tetris web game",
        description="A playable browser Tetris.",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="tetris-web",
        created_by="ceo",
    )


def _plan_args() -> dict[str, JsonValue]:
    """Build a submission the plan parser accepts.

    Returns:
        The planner's tool arguments.
    """
    return {
        "subtasks": [
            {
                "id": "s1",
                "title": "Board renderer",
                "description": "Render the grid",
                "stakes": "normal",
                "required_role": "Frontend Engineer",
                "expected_artifacts": ["src/board.tsx"],
                "acceptance_criteria": ["grid renders"],
            },
        ],
        "task_structure": "sequential",
        "coordination_topology": "auto",
    }


def _strategy(
    provider: ScriptedProvider, states: _RecordingStates | None
) -> AgentSessionDecompositionStrategy:
    """Build the planning strategy over *provider*.

    Returns:
        The strategy, recording liveness into *states* when one is given.
    """
    return AgentSessionDecompositionStrategy(
        provider_selector=lambda _identity: provider,
        fallback=_SentinelFallback(),
        deps=DecompositionStrategyDeps(
            agent_session_config=AgentSessionDecompositionConfig(max_turns=4),
            agent_states=None if states is None else lambda: states,
        ),
    )


class TestThePlanningSessionIsVisibleWhileItRuns:
    async def test_it_claims_a_live_row_for_the_owner(self) -> None:
        states = _RecordingStates()
        provider = ScriptedProvider(
            [
                build_tool_call_response("submit_decomposition_plan", _plan_args()),
                make_text_response("done"),
            ]
        )
        owner = make_e2e_identity()

        await _strategy(provider, states).decompose(
            _task(), DecompositionContext(owner_identity=owner)
        )

        assert len(states.saved) == 1
        claimed = states.saved[0]
        assert claimed.agent_id == str(owner.id)
        assert claimed.status is ExecutionStatus.EXECUTING

    async def test_it_releases_the_row_when_the_session_ends(self) -> None:
        states = _RecordingStates()
        provider = ScriptedProvider(
            [
                build_tool_call_response("submit_decomposition_plan", _plan_args()),
                make_text_response("done"),
            ]
        )
        owner = make_e2e_identity()

        await _strategy(provider, states).decompose(
            _task(), DecompositionContext(owner_identity=owner)
        )

        assert len(states.cleared) == 1
        agent_id, execution_id = states.cleared[0]
        assert agent_id == str(owner.id)
        # Named, so a sibling planning session's row is left alone. Recursion
        # runs a session per subtree and one owner can hold several.
        assert execution_id == states.saved[0].execution_id

    async def test_a_session_that_failed_still_releases_the_row(self) -> None:
        # The release is in a ``finally`` for exactly this: a row left
        # EXECUTING makes a finished agent look occupied for the life of the
        # process, and every surface then reports work that is not happening.
        #
        # Scripted to spend every turn without submitting, which is a session
        # that ran on its own terms and produced nothing: it raises rather than
        # falling back, so the failure reaches the context manager.
        states = _RecordingStates()
        provider = ScriptedProvider(
            [make_text_response("I will not plan this") for _ in range(4)]
        )

        with pytest.raises(DecompositionError):
            await _strategy(provider, states).decompose(
                _task(), DecompositionContext(owner_identity=make_e2e_identity())
            )

        assert len(states.cleared) == 1

    async def test_no_repository_plans_without_recording_liveness(self) -> None:
        # The complement, so the claim above cannot be a hard dependency: a
        # deployment whose persistence is not connected still plans.
        provider = ScriptedProvider(
            [
                build_tool_call_response("submit_decomposition_plan", _plan_args()),
                make_text_response("done"),
            ]
        )

        plan = await _strategy(provider, None).decompose(
            _task(), DecompositionContext(owner_identity=make_e2e_identity())
        )

        assert len(plan.subtasks) == 1
