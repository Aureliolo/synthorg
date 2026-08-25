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
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.agent import AgentIdentity
from synthorg.core.role_catalog import role_is_gate_role
from synthorg.core.types import NotBlankStr, PersonaLabelStr
from synthorg.engine.decomposition.atomicity import SubtaskAtomicityPolicy


def roster_from_agents(agents: Sequence[AgentIdentity]) -> tuple[NotBlankStr, ...]:
    """Return the distinct roles a set of agents staffs, in a stable order.

    Derived from the agents themselves rather than from the role catalogue: a
    role nobody holds cannot own a plan item any more than an invented one
    can, so the planner is offered what can actually be dispatched to.

    A gate role is held by ordinary roster agents and is therefore staffed, but
    it JUDGES work rather than performing it, so it is not something a plan item
    can be owned by. Offered one, a planner takes it: a live run put 19 of 102
    subtasks under ``Completion Reviewer``, seven of them atomic and due to
    execute, which makes the party that judges the author of what it judges and
    puts plan-level verification inside every arm of an experiment measuring it.

    Args:
        agents: The agents available to the plan.

    Returns:
        Each role once, sorted, so the prompt and the schema enum are stable
        across runs and a fingerprint test can pin them.
    """
    return tuple(
        sorted(
            {agent.role for agent in agents if not role_is_gate_role(str(agent.role))}
        )
    )


class DecompositionContext(BaseModel):
    """Configuration context for a decomposition operation.

    Attributes:
        max_subtasks: How many units one level may produce, or ``None`` to
            take the operator's ``coordination.decomposition_max_subtasks``.
        max_depth: How many levels of planning are allowed, or ``None`` to
            take the operator's ``coordination.decomposition_max_depth``.
            Both are runaway backstops rather than targets: what decides a
            split is whether a unit is one agent's worth of work, so a small
            objective stops on its own well short of either. ``None`` is the
            normal value, and ``DecompositionService`` resolves both once at
            the root and stamps them, so every level of one tree is planned
            under one budget and a caller that declares one still wins.
        current_depth: Current nesting depth.
        atomicity: The size signal this level is held to at PARSE time, set
            only where no further level is available so an oversized unit
            cannot be delegated downward. ``None`` everywhere else, which is
            what keeps a level with depth left splitting rather than
            correcting. ``DecompositionService`` is the single owner of that
            judgement and stamps this per level.
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

    max_subtasks: int | None = Field(
        default=None,
        ge=1,
        description="Units one level may produce; None takes the setting",
    )
    max_depth: int | None = Field(
        default=None,
        ge=1,
        description="Levels of planning allowed; None takes the setting",
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
    atomicity: SubtaskAtomicityPolicy | None = Field(
        default=None,
        description="Size signal enforced at parse time on the last level",
    )


#: Mirror of ``coordination.decomposition_max_depth``. Held here because a
#: harness runs with no settings at all, and the answer has to stand there too.
DEFAULT_MAX_DEPTH: Final[int] = 5

#: Mirror of ``coordination.decomposition_max_subtasks``, for the same reason.
DEFAULT_MAX_SUBTASKS: Final[int] = 10


def depth_budget(context: DecompositionContext) -> int:
    """How many levels of planning *context* is allowed.

    The one place an undeclared, unresolved backstop falls back, so the answer
    cannot differ between the several readers that ask it. Every context below
    ``DecompositionService.decompose_task`` is resolved and carries the
    operator's own value, so the fallback is reached only by a harness that
    built a context and never handed it to the service.

    Args:
        context: The level being planned.

    Returns:
        The declared or resolved backstop, else the definition's own default.
    """
    return DEFAULT_MAX_DEPTH if context.max_depth is None else context.max_depth


def width_budget(context: DecompositionContext) -> int:
    """How many units one level of *context* may produce.

    Args:
        context: The level being planned.

    Returns:
        The declared or resolved backstop, else the definition's own default.
    """
    return (
        DEFAULT_MAX_SUBTASKS if context.max_subtasks is None else context.max_subtasks
    )


__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_SUBTASKS",
    "DecompositionContext",
    "depth_budget",
    "roster_from_agents",
    "width_budget",
]
