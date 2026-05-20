"""Production :class:`AgentRunner` that dispatches via :class:`AgentEngine`.

Builds a transient :class:`Task` carrying the red-team system prompt
in its ``description``, plus per-evaluation ``acceptance_criteria``,
then delegates to :class:`AgentEngine.run`. The
:class:`SubmitRedTeamReportTool` is registered once on the engine's
tool registry at boot, so each evaluation reuses the same tool
instance; per-evaluation state (``execution_id``, ``task_id``) flows
through the tool's arguments.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.core.enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.red_team import RED_TEAM_AGENT_FAILED
from synthorg.security.redteam.errors import RedTeamDispatchError
from synthorg.security.redteam.models import RedTeamReviewInput  # noqa: TC001
from synthorg.security.redteam.prompt import build_red_team_system_prompt

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.agent_engine import AgentEngine

logger = get_logger(__name__)

_RED_TEAM_TASK_TYPE: TaskType = TaskType.REVIEW
"""The transient task is classified as a REVIEW work item.

The red-team's job is to attack the deliverable, which fits naturally
under the REVIEW :class:`TaskType` already in the catalogue: it gates
completion on a structured assessment.
"""

_RED_TEAM_TASK_PRIORITY: Priority = Priority.HIGH
"""HIGH priority: red-team review runs at the completion edge."""

_RED_TEAM_PROJECT: NotBlankStr = "synthorg-red-team"
"""Project scope used for the transient red-team review task."""

_RED_TEAM_TITLE: NotBlankStr = "Adversarial red-team review"


class AgentEngineRunner:
    """Production :class:`AgentRunner` backed by :class:`AgentEngine.run`.

    Implements :class:`synthorg.security.redteam.protocol.AgentRunner`.

    Args:
        engine: Boot-wired :class:`AgentEngine` with the
            :class:`SubmitRedTeamReportTool` already registered on its
            tool registry.
        identity: The red-team :class:`AgentIdentity` built via
            :func:`build_red_team_agent_identity`.
    """

    def __init__(
        self,
        *,
        engine: AgentEngine,
        identity: AgentIdentity,
    ) -> None:
        self._engine = engine
        self._identity = identity

    async def run(
        self,
        *,
        review_input: RedTeamReviewInput,
    ) -> None:
        """Dispatch the red-team agent for ``review_input``.

        The agent's only side effect is filing exactly one report via
        ``submit_red_team_report``; the gate reads the report from the
        repository after this call returns.

        Raises:
            RedTeamDispatchError: When :class:`AgentEngine.run` itself
                raises. The gate translates this into a fail-OPEN
                informational finding.
        """
        prompt = build_red_team_system_prompt(review_input)
        task = self._build_transient_task(review_input, prompt)
        try:
            await self._engine.run(identity=self._identity, task=task)
        except Exception as exc:
            logger.warning(
                RED_TEAM_AGENT_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                "Red-team AgentEngine.run failed for review_input "
                f"execution_id={review_input.execution_id!r}"
            )
            raise RedTeamDispatchError(msg) from exc

    @staticmethod
    def _build_transient_task(
        review_input: RedTeamReviewInput,
        prompt: NotBlankStr,
    ) -> Task:
        """Construct the transient :class:`Task` the agent sees."""
        criteria = tuple(
            AcceptanceCriterion(description=criterion)
            for criterion in review_input.acceptance_criteria
        )
        return Task(
            id=f"red-team-{uuid4().hex}",
            title=_RED_TEAM_TITLE,
            description=prompt,
            type=_RED_TEAM_TASK_TYPE,
            priority=_RED_TEAM_TASK_PRIORITY,
            project=_RED_TEAM_PROJECT,
            created_by=review_input.assigned_agent_id,
            acceptance_criteria=criteria,
            status=TaskStatus.IN_PROGRESS,
            estimated_complexity=Complexity.SIMPLE,
        )
