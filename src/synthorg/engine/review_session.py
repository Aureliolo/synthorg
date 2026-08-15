# module-kind: code
"""The identity a quality gate dispatches its judge under.

A gate judges an artefact another agent wrote, so the content it reads is
attacker-controlled in the way any deliverable is: an injection planted in
the work under review executes inside the reviewing session. What that
injection can reach is decided entirely by the identity the gate dispatches.

A roster agent carries the grants its operator gave it for its day job, and
those can be broad: ELEVATED tool access, wildcard MCP capabilities, FULL
autonomy. Judging needs none of that. It needs to read the deliverable,
build and test it, and file one verdict. So the gate reviews under a
narrowed copy of the selected agent rather than the agent as the roster
holds it, and the blast radius of a planted injection stops depending on how
privileged the judge happens to be.

This narrows the SESSION, never the roster: the agent keeps its own grants
everywhere else, and the copy exists only for the duration of the dispatch.
It is the same argument the selection ladder already makes about capability,
applied to authority instead: a verdict should turn on the work, not on who
was free to look at it.
"""

from typing import Final

from synthorg.core.agent import AgentIdentity, ToolPermissions
from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.tool_constraints import ToolAccessLevel

#: Tool surface a judging session runs with. STANDARD covers reading the
#: deliverable and running the build and test commands a verdict rests on.
#: ``mcp_capabilities`` is empty on purpose: the internal MCP surface is how
#: an agent reaches the rest of the org, and nothing about judging one
#: deliverable needs it.
REVIEW_TOOL_PERMISSIONS: Final[ToolPermissions] = ToolPermissions(
    access_level=ToolAccessLevel.STANDARD,
    mcp_capabilities=(),
)

#: Autonomy a judging session runs at. SUPERVISED so anything the session
#: tries beyond reading and testing meets the ordinary approval gate rather
#: than an autonomy grant written for work the agent does elsewhere.
REVIEW_AUTONOMY_LEVEL: Final[AutonomyLevel] = AutonomyLevel.SUPERVISED


def as_review_session(reviewer: AgentIdentity) -> AgentIdentity:
    """Return *reviewer* narrowed to what judging needs.

    Args:
        reviewer: The roster agent the gate selected.

    Returns:
        A copy holding the review tool surface and autonomy. Identity,
        role, department and bound model are untouched, so the verdict is
        still attributed to the real agent and still runs on the pair its
        operator chose.
    """
    return reviewer.model_copy(
        update={
            "tools": REVIEW_TOOL_PERMISSIONS,
            "autonomy_level": REVIEW_AUTONOMY_LEVEL,
        }
    )


def in_gate_dispatch() -> bool:
    """Report whether the caller is running inside a quality gate's dispatch.

    Both gates bind a trusted runtime context around the agent run they
    drive, and a contextvar is task-scoped, so this is true for the judging
    session and its children and false everywhere else.

    Callers use it to scope a privilege to the judging itself rather than to
    the agent that happens to hold the role: cross-project reach is granted
    because a gate judges work across the org, so it belongs to the review
    and not to the reviewer's ordinary working life.

    Returns:
        ``True`` while a completion-oracle or red-team dispatch is in flight.
    """
    # Cycle breakers: both gate packages import the runners, which import
    # this module for the narrowing above. Kept local so the dependency runs
    # one way at import time and both ways only at call time.
    from synthorg.engine.completion_oracle.runtime_context import (  # noqa: PLC0415
        get_completion_oracle_runtime_context,
    )
    from synthorg.security.redteam.runtime_context import (  # noqa: PLC0415
        get_red_team_runtime_context,
    )

    return (
        get_completion_oracle_runtime_context() is not None
        or get_red_team_runtime_context() is not None
    )


__all__ = [
    "REVIEW_AUTONOMY_LEVEL",
    "REVIEW_TOOL_PERMISSIONS",
    "as_review_session",
    "in_gate_dispatch",
]
