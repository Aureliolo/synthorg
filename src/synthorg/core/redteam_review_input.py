"""Red-team gate input value object.

``RedTeamReviewInput`` is what the adversarial review gate sees on entry: a
deliverable plus the context the gate needs to attack it. It is assembled in the
engine and consumed by the security red-team subsystem, so it lives in a
dependency-free ``core`` leaf (it needs only the string and autonomy-level
primitives) rather than the heavy ``security.redteam`` package, letting either
side annotate against it at module level without a cross-package cycle.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr


class RedTeamReviewInput(BaseModel):
    """What the gate sees on entry: the deliverable plus its context.

    The gate's evaluation surface. Lives here (not on Task directly) so
    the red-team subsystem can be exercised without dragging the full
    Task model and its dependencies into every test.

    Attributes:
        task_id: The deliverable's owning task.
        execution_id: The execution that produced the deliverable.
        deliverable_content: The artifact text the red-team attacks.
        acceptance_criteria: The brief's acceptance criteria, used by
            the agent prompt; a dedicated requirements-coverage checker
            (not yet built) would also consume it.
        assigned_agent_id: The agent that produced the deliverable
            (forbidden as red-team reviewer; enforced one layer up by
            the review-gate's self-review guard).
        autonomy: Effective autonomy level governing severity-tiered
            routing in :mod:`synthorg.security.redteam.routing`.
        project_id: Owning project of the deliverable, when known. The
            substrate-backed grounding checker scopes its corpus search
            to it; ``None`` falls back to a global-only search.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_id: NotBlankStr
    execution_id: NotBlankStr
    deliverable_content: NotBlankStr
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(min_length=1)
    assigned_agent_id: NotBlankStr
    autonomy: AutonomyLevel
    project_id: NotBlankStr | None = None
