# module-kind: declarative
"""What a decomposition runs UNDER, and the roster that fills it in.

Apart from :mod:`synthorg.engine.decomposition.models`, which describes what a
decomposition IS. The two are asked for at different moments: the context is
assembled by whoever is about to plan, from what the org staffs and what the
workspace holds, while the models are what the planning produced.

``roster_from_agents`` lives here rather than beside the models because the
only thing it exists to fill is this context's ``available_roles``.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr, PersonaLabelStr


def roster_from_agents(agents: Sequence[AgentIdentity]) -> tuple[NotBlankStr, ...]:
    """Return the distinct roles a set of agents staffs, in a stable order.

    Derived from the agents themselves rather than from the role catalogue: a
    role nobody holds cannot own a plan item any more than an invented one
    can, so the planner is offered what can actually be dispatched to.

    Args:
        agents: The agents available to the plan.

    Returns:
        Each role once, sorted, so the prompt and the schema enum are stable
        across runs and a fingerprint test can pin them.
    """
    return tuple(sorted({agent.role for agent in agents}))


class DecompositionContext(BaseModel):
    """Configuration context for a decomposition operation.

    Attributes:
        max_subtasks: Maximum number of subtasks allowed.
        max_depth: Maximum nesting depth for recursive decomposition.
        current_depth: Current nesting depth.
        workspace_summary: What the project workspace actually holds, for the
            planner to plan against. ``None`` when the caller cannot resolve
            it, which leaves the brief's unconditional rule to carry the
            point: a planner that is told nothing must assume nothing.
        owner_identity: The accountable owner staffed for this initiative,
            or ``None`` when the initiative is unowned. An agent-session
            decomposition strategy plans AS this owner (its persona, tools,
            and memory); a single-shot strategy ignores it.
        available_roles: The roles the org actually staffs, so the planner
            selects an owner rather than inventing one. Empty means "no
            roster known", which leaves the owner a free string and skips the
            check: an org with no agents has nothing to validate against.
            Typed as persona labels rather than plain non-blank strings: a
            role name is operator-authored, and these go into the SYSTEM
            prompt and the tool schema, where a newline or an angle bracket
            would be a forged instruction line or a forged content fence
            rather than a funny-looking job title.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    max_subtasks: int = Field(
        default=10,
        ge=1,
        description="Maximum number of subtasks allowed",
    )
    max_depth: int = Field(
        default=3,
        ge=1,
        description="Maximum nesting depth",
    )
    current_depth: int = Field(
        default=0,
        ge=0,
        description="Current nesting depth",
    )
    workspace_summary: str | None = Field(
        default=None,
        description="What the project workspace actually contains, or None "
        "when the caller cannot resolve it",
    )
    owner_identity: AgentIdentity | None = Field(
        default=None,
        description="Accountable owner the planning agent-session runs as",
    )
    available_roles: tuple[PersonaLabelStr, ...] = Field(
        default=(),
        description="Roles the org staffs, which an owner must be drawn from",
    )


__all__ = ["DecompositionContext", "roster_from_agents"]
