"""The glue between a rework verdict and something that acts on it.

Three well-tested units sat either side of this and nothing named the wiring
between them, so deleting the settle call or breaking the loop unconditionally
left the suite green. What is pinned here is the pair a dispatch actually
depends on: while rounds remain the reviewer's reason comes back as a context
to re-run, and when they are spent the task lands FAILED rather than resting
IN_PROGRESS with no loop behind it and nothing watching.
"""

from datetime import UTC, date, datetime

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import ExecutionResult, TerminationReason
from synthorg.engine.loop_rework import (
    DEFAULT_MAX_REWORK_ROUNDS,
    REWORK_METADATA_KEY,
)
from synthorg.engine.post_execution.rework_settlement import (
    resolve_rework_bound,
    rework_continuation,
    rework_reason,
    settle_unresolved_rework,
)
from synthorg.engine.task_execution import TaskExecution
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import as_uuid, mock_of, sid

pytestmark = pytest.mark.unit

_REASON = "Code task produced no test run; there is no evidence the work builds."


def _task() -> Task:
    return Task(
        id=as_uuid("task-rework-1"),
        title="Draw the board",
        description="Render the grid",
        type=TaskType.DEVELOPMENT,
        project=sid("proj-tetris"),
        created_by="Ada Chen",
        assigned_to=sid("agent-1"),
        status=TaskStatus.IN_PROGRESS,
    )


def _run(*, sent_back: bool) -> ExecutionResult:
    context = AgentContext(
        execution_id="exec-rework-1",
        identity=AgentIdentity(
            name="Ada Chen",
            role="developer",
            department="engineering",
            model=ModelConfig(provider="test-provider", model_id="test-basic-001"),
            hiring_date=date(2026, 1, 1),
        ),
        task_execution=TaskExecution(task=_task(), status=TaskStatus.IN_PROGRESS),
        started_at=datetime.now(UTC),
    )
    return ExecutionResult(
        context=context,
        termination_reason=TerminationReason.COMPLETED,
        metadata={REWORK_METADATA_KEY: _REASON} if sent_back else {},
    )


class TestTheDiscriminator:
    """One reader decides "was this sent back", and both ends ask it."""

    def test_a_sent_back_run_carries_the_reviewers_reason(self) -> None:
        assert rework_reason(_run(sent_back=True)) == _REASON

    def test_an_ordinary_run_carries_none(self) -> None:
        assert rework_reason(_run(sent_back=False)) is None

    def test_a_non_string_under_the_key_is_not_a_reason(self) -> None:
        # ``metadata`` is an untyped forward-compat bag, so the key's presence
        # is not the question; whether it holds words a human can read is.
        run = _run(sent_back=False).model_copy(
            update={"metadata": {REWORK_METADATA_KEY: 3}}
        )

        assert rework_reason(run) is None


class TestContinuation:
    def test_a_sent_back_run_comes_back_as_a_context_to_re_run(self) -> None:
        resumed = rework_continuation(
            _run(sent_back=True),
            rounds_taken=0,
            max_rounds=DEFAULT_MAX_REWORK_ROUNDS,
        )

        assert resumed is not None
        last = resumed.conversation[-1]
        assert last.content is not None
        assert _REASON in last.content

    def test_an_ordinary_run_produces_no_continuation(self) -> None:
        assert (
            rework_continuation(
                _run(sent_back=False),
                rounds_taken=0,
                max_rounds=DEFAULT_MAX_REWORK_ROUNDS,
            )
            is None
        )

    def test_a_spent_bound_produces_no_continuation(self) -> None:
        # Told apart from the ordinary case afterwards by the reason the run
        # still carries, which is what makes the settlement below fire on one
        # and not the other.
        assert (
            rework_continuation(
                _run(sent_back=True),
                rounds_taken=DEFAULT_MAX_REWORK_ROUNDS,
                max_rounds=DEFAULT_MAX_REWORK_ROUNDS,
            )
            is None
        )


class TestSettlement:
    async def test_a_run_that_stopped_reworking_lands_failed(self) -> None:
        settled = await settle_unresolved_rework(
            _run(sent_back=True),
            agent_id=sid("agent-1"),
            task_id=str(as_uuid("task-rework-1")),
            rounds_taken=DEFAULT_MAX_REWORK_ROUNDS,
            task_engine=None,
            approval_store=None,
        )

        assert settled.context.task_execution is not None
        assert settled.context.task_execution.status is TaskStatus.FAILED

    async def test_the_landing_names_the_refusal_it_could_not_clear(self) -> None:
        """ "It stopped" is not a diagnosis, and the rounds are the cost."""
        settled = await settle_unresolved_rework(
            _run(sent_back=True),
            agent_id=sid("agent-1"),
            task_id=str(as_uuid("task-rework-1")),
            rounds_taken=DEFAULT_MAX_REWORK_ROUNDS,
            task_engine=None,
            approval_store=None,
        )

        assert settled.context.task_execution is not None
        recorded = settled.context.task_execution.transition_log[-1].reason
        assert _REASON in recorded
        assert str(DEFAULT_MAX_REWORK_ROUNDS + 1) in recorded

    async def test_an_ordinary_run_is_left_exactly_as_it_was(self) -> None:
        # The settlement runs on every dispatch, so a run no review sent back
        # must pass through it untouched rather than being failed by it.
        finished = _run(sent_back=False)

        settled = await settle_unresolved_rework(
            finished,
            agent_id=sid("agent-1"),
            task_id=str(as_uuid("task-rework-1")),
            rounds_taken=0,
            task_engine=None,
            approval_store=None,
        )

        assert settled is finished


class TestTheBound:
    """Each round is a whole run, so the number is the operator's."""

    async def test_an_unwired_resolver_falls_back_to_the_shipped_default(
        self,
    ) -> None:
        assert await resolve_rework_bound(None) == DEFAULT_MAX_REWORK_ROUNDS

    async def test_the_configured_value_is_what_a_dispatch_uses(self) -> None:
        resolver = mock_of[ConfigResolverProtocol]()
        resolver.get_int.return_value = 5

        assert await resolve_rework_bound(resolver) == 5
