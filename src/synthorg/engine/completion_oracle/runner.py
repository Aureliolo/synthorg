# module-kind: code
"""Production :class:`ReviewerAgentRunner` dispatching via :class:`AgentEngine`.

Builds a transient :class:`Task` carrying the reviewer system prompt in its
``description`` plus the deliverable's ``acceptance_criteria``, then delegates
to :class:`AgentEngine.run`. The :class:`SubmitCompletionOracleVerdictTool`
is registered once on the engine's tool registry at boot, so each evaluation
reuses the same tool instance; per-evaluation state flows through the trusted
runtime context and the tool arguments.
"""

import asyncio
from typing import Final
from uuid import uuid4

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.completion_oracle.errors import CompletionOracleDispatchError
from synthorg.engine.completion_oracle.prompt import (
    build_completion_reviewer_system_prompt,
)
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.review_session import as_review_session
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_AGENT_FAILED,
)

logger = get_logger(__name__)

_REVIEWER_TASK_TYPE: Final[TaskType] = TaskType.REVIEW
"""The transient task is a REVIEW work item: it gates completion on a
structured independent assessment."""

_REVIEWER_TASK_PRIORITY: Final[Priority] = Priority.HIGH
"""HIGH priority: peer review runs at the completion edge."""

_REVIEWER_TITLE: Final[NotBlankStr] = "Independent completion review"


class ReviewerAgentEngineRunner:
    """Production :class:`ReviewerAgentRunner` backed by :class:`AgentEngine.run`.

    Holds no identity of its own: the reviewer is selected from the roster
    per review and arrives with each dispatch.

    Args:
        engine: Boot-wired :class:`AgentEngine` with the
            :class:`SubmitCompletionOracleVerdictTool` already registered.
    """

    def __init__(
        self,
        *,
        engine: AgentEngine,
    ) -> None:
        self._engine = engine

    async def run(
        self,
        *,
        review_input: CompletionOracleReviewInput,
        reviewer: AgentIdentity,
    ) -> ModelConfig | None:
        """Dispatch ``reviewer`` against ``review_input``.

        The agent's only side effect is filing exactly one verdict via
        ``submit_completion_oracle_verdict``; the gate reads it from the
        repository after this call returns.

        Returns:
            The pair the review actually ran on, which routing or the budget
            may have moved off the reviewer's roster binding, so the archive
            records what produced the verdict rather than what was selected.
            ``None`` when the run committed to no binding.

        Raises:
            asyncio.CancelledError: Propagated when the run is cancelled.
            CompletionOracleDispatchError: When :class:`AgentEngine.run`
                itself raises. The gate translates this into an ESCALATE.
        """
        prompt = build_completion_reviewer_system_prompt(review_input)
        # The session is narrowed, not the agent: the deliverable it is about
        # to read was written by something else and may carry an injection,
        # and what that reaches must not depend on the grants this particular
        # holder happens to carry for its ordinary work.
        session = as_review_session(reviewer)
        task = self._build_transient_task(review_input, prompt, session)
        try:
            result = await self._engine.run(identity=session, task=task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COMPLETION_ORACLE_AGENT_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                "Completion-reviewer AgentEngine.run failed for review_input "
                f"execution_id={review_input.execution_id!r}"
            )
            raise CompletionOracleDispatchError(msg) from exc
        return result.bound_model

    @staticmethod
    def _build_transient_task(
        review_input: CompletionOracleReviewInput,
        prompt: NotBlankStr,
        reviewer: AgentIdentity,
    ) -> Task:
        """Construct the transient :class:`Task` the reviewer agent sees.

        The task carries the REVIEWED work's project, because the engine
        validates that a task's project exists before it dispatches: a
        constant here would name a project no repository holds, and every
        review would raise ``ProjectNotFoundError`` before a single token was
        spent while the gate fail-closed to ESCALATE. The tail's own
        transient tasks carry the real project for the same reason.

        It also carries the REVIEWED work's stakes and complexity. Judging a
        deliverable is as consequential as producing it, and the reviewer was
        already chosen for that requirement, so the review runs at the same
        bar rather than at a bar this module invented.

        Returns:
            The transient ``Task`` carrying the reviewer prompt and criteria,
            assigned to the selected reviewer.

        Raises:
            CompletionOracleDispatchError: When the review input names no
                project. The gate translates it into an ESCALATE, which is
                the fail-closed answer; inventing a project id here is what
                made the failure silent in the first place.
        """
        if review_input.project_id is None:
            msg = (
                "Completion-reviewer dispatch needs the reviewed task's "
                f"project; review_input for task {review_input.task_id!r} "
                "names none"
            )
            raise CompletionOracleDispatchError(msg)
        criteria = tuple(
            AcceptanceCriterion(description=criterion)
            for criterion in review_input.acceptance_criteria
        )
        return Task(
            id=uuid4(),
            title=_REVIEWER_TITLE,
            description=prompt,
            type=_REVIEWER_TASK_TYPE,
            priority=_REVIEWER_TASK_PRIORITY,
            project=review_input.project_id,
            created_by=str(reviewer.id),
            assigned_to=str(reviewer.id),
            acceptance_criteria=criteria,
            status=TaskStatus.IN_PROGRESS,
            estimated_complexity=review_input.estimated_complexity,
            stakes=review_input.stakes,
        )
