"""A review that says rework has to reach something that can run again.

A status write alone leaves the task with no loop behind it: nothing drives a
task except a coordination wave, and the wave that ran it has already returned
by the time the review lands. So the REWORK verdict re-runs the agent in place,
bounded, and these pin what that boundedness owes.
"""

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_rework import (
    MAX_REWORK_ROUNDS,
    REWORK_EXHAUSTED_REASON,
    REWORK_NUDGE,
    continue_rework,
)
from synthorg.providers.enums import MessageRole

pytestmark = pytest.mark.unit

_REASON = "Code task produced no test run; there is no evidence the work builds."


def _ctx(identity: AgentIdentity, task: Task) -> AgentContext:
    return AgentContext.from_identity(identity, task=task)


class TestContinueRework:
    def test_the_reviewers_own_words_reach_the_agent(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Quoted, not paraphrased.

        The gate's reason is the only thing that says what would satisfy it,
        and a paraphrase is how "no test run" becomes "try harder".
        """
        resumed = continue_rework(
            _ctx(sample_agent_with_personality, sample_task_with_criteria),
            _REASON,
            rounds_taken=0,
            execution_id="exec-1",
        )

        assert resumed is not None
        nudge = resumed.conversation[-1]
        assert nudge.role is MessageRole.USER
        assert nudge.content is not None
        assert _REASON in nudge.content

    def test_the_run_keeps_the_work_it_already_did(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """Resumed, not restarted: the correction extends the same context."""
        before = _ctx(sample_agent_with_personality, sample_task_with_criteria)

        resumed = continue_rework(
            before, _REASON, rounds_taken=0, execution_id="exec-1"
        )

        assert resumed is not None
        assert resumed.conversation[:-1] == before.conversation

    @pytest.mark.parametrize("rounds", list(range(MAX_REWORK_ROUNDS)))
    def test_every_round_inside_the_bound_is_taken(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
        rounds: int,
    ) -> None:
        assert (
            continue_rework(
                _ctx(sample_agent_with_personality, sample_task_with_criteria),
                _REASON,
                rounds_taken=rounds,
                execution_id="exec-1",
            )
            is not None
        )

    def test_the_bound_is_spent_rather_than_looping(
        self,
        sample_agent_with_personality: AgentIdentity,
        sample_task_with_criteria: Task,
    ) -> None:
        """A model told twice why it was refused will not hear it a third time.

        Each round is a whole run, so the bound is a spend limit as much as a
        progress one.
        """
        assert (
            continue_rework(
                _ctx(sample_agent_with_personality, sample_task_with_criteria),
                _REASON,
                rounds_taken=MAX_REWORK_ROUNDS,
                execution_id="exec-1",
            )
            is None
        )

    def test_the_exhausted_reason_names_the_refusal_it_could_not_clear(
        self,
    ) -> None:
        """ "It stopped" is not a diagnosis; the gate's reason is."""
        reason = REWORK_EXHAUSTED_REASON.format(rounds=3, reason=_REASON)

        assert _REASON in reason
        assert "3" in reason

    def test_the_nudge_asks_for_evidence_not_assurance(self) -> None:
        """The refusal that produced this is precisely "you claimed, not ran"."""
        nudge = REWORK_NUDGE.format(reason=_REASON)

        assert "claim that they pass is not evidence" in nudge
