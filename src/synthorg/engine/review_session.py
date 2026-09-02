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
from synthorg.core.tool_constraints import (
    GitAccess,
    TerminalAccess,
    ToolAccessLevel,
    ToolSubConstraints,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.tool_names import (
    SUBMIT_COMPLETION_ORACLE_VERDICT_TOOL_NAME,
)
from synthorg.security.autonomy.enums import ToolCategory

#: Tool surface a judging session runs with. STANDARD covers reading the
#: deliverable; what it also grants (writing files, running commands,
#: committing) is withdrawn below, because a judge that writes is authoring
#: what it judges. A recorded corpus put 36 file-writing shell calls in
#: sessions whose only job was to file a verdict. The build and test
#: evidence a verdict rests on is not something the reviewer produces: the
#: completion gates run the project's declared commands before the review
#: opens and hand the recorded runs to the session, so the reviewer reads
#: evidence rather than manufacturing it inside the tree under review.
#: ``mcp_capabilities`` is empty on purpose: the internal MCP surface is how
#: an agent reaches the rest of the org, and nothing about judging one
#: deliverable needs it.
#:
#: The verdict tool is allowed BY NAME because it is the one thing a judging
#: session exists to do and STANDARD does not reach it: the tool is
#: ``ToolCategory.OTHER``, which only ELEVATED admits. Without this the
#: reviewer is handed the tool in its registry and refused at the invoke
#: boundary, which is what a live run produced: two attempts at
#: ``submit_completion_oracle_verdict``, both
#: ``Category 'other' is not permitted at access level 'standard'``, then the
#: session flailing through ``list_tools`` and ``shell_command`` looking for
#: another way to file. Naming the one tool keeps the narrowing honest;
#: raising the level to ELEVATED would hand a reviewer every other category
#: too, which is exactly what the narrowing exists to prevent.
#: Categories a judging session is barred from whatever STANDARD grants.
#: ``EXTERNAL_DATA`` is the category every governed connection tool carries
#: (forge, chat, deploy, publish) plus the external-API and research tools:
#: everything that reaches outside the organisation. Judging a deliverable
#: needs none of it, and an injection planted in the artefact under review
#: runs inside this session, so a reviewer that could reach ``deploy_release``
#: could be made to file a production-deploy approval request under a role an
#: operator trusts to judge rather than to originate. Withheld by CATEGORY
#: rather than by name because a name list re-opens the hole the day a tool
#: joins the category.
#:
#: ``TERMINAL`` and ``CODE_EXECUTION`` are withheld for the other half of the
#: same argument: a shell is how a reviewing session came to write files at
#: all, and anything it runs inside the reviewed tree runs code the author
#: of that tree chose. The build and test runs a verdict cites were recorded
#: by the gates before the review opened.
REVIEW_DENIED_CATEGORIES: Final[tuple[ToolCategory, ...]] = (
    ToolCategory.EXTERNAL_DATA,
    ToolCategory.TERMINAL,
    ToolCategory.CODE_EXECUTION,
)

#: Tools withheld BY NAME because they sit in categories the reviewer keeps.
#: ``FILE_SYSTEM`` stays granted for reading the deliverable and
#: ``VERSION_CONTROL`` for reading its history, so the mutating members of
#: each are named: a category-level denial would take ``read_file`` and
#: ``git_diff`` with them, and a reviewer that cannot open the artefact
#: cannot judge it. A name list is the weaker shape (a tool joining the
#: category later is not covered), which is why the categories that CAN be
#: withheld whole are, above, and only these two are held by name.
#: ``git_branch`` is named because ``GitAccess.LOCAL_ONLY`` below withholds
#: only what reaches a remote: creating, switching or deleting a branch
#: rewrites the checkout under review without leaving the machine.
REVIEW_DENIED_TOOLS: Final[tuple[NotBlankStr, ...]] = (
    NotBlankStr("write_file"),
    NotBlankStr("edit_file"),
    NotBlankStr("delete_file"),
    NotBlankStr("git_commit"),
    NotBlankStr("git_branch"),
)

#: Sub-constraints holding the same line at the enforcer that runs after
#: category gating: no terminal access however a command is spelled, and git
#: confined to local reads so nothing a reviewer does can reach a remote.
REVIEW_SUB_CONSTRAINTS: Final[ToolSubConstraints] = ToolSubConstraints(
    terminal=TerminalAccess.NONE,
    git=GitAccess.LOCAL_ONLY,
)

REVIEW_TOOL_PERMISSIONS: Final[ToolPermissions] = ToolPermissions(
    access_level=ToolAccessLevel.STANDARD,
    allowed=(SUBMIT_COMPLETION_ORACLE_VERDICT_TOOL_NAME,),
    denied=REVIEW_DENIED_TOOLS,
    denied_categories=REVIEW_DENIED_CATEGORIES,
    mcp_capabilities=(),
    sub_constraints=REVIEW_SUB_CONSTRAINTS,
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


__all__ = [
    "REVIEW_AUTONOMY_LEVEL",
    "REVIEW_DENIED_CATEGORIES",
    "REVIEW_DENIED_TOOLS",
    "REVIEW_SUB_CONSTRAINTS",
    "REVIEW_TOOL_PERMISSIONS",
    "as_review_session",
]
