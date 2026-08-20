# module-kind: code
"""What a project's workspace actually holds, in one line for a prompt.

The planning brief forbids assuming any file exists, but a prohibition only
stops the planner asserting; it cannot tell it what is true. A live run planned
a brand-new project on seven filenames its org-wide recall had picked up from a
different project, scoped all ten items as integration rather than
construction, and passed a four-reviewer panel that never questioned the
premise. The workspace did not exist.

So the brief states the inventory as a fact. An absent workspace and an
unlisted one read identically to a planner, and only one of them means there is
nothing there, so absence is spelled out rather than left to silence.

``.git`` is excluded deliberately: every provisioned workspace has one, and
naming it in a list of what the project holds reads as work already done.
"""

import asyncio
from pathlib import Path
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace.paths import project_workspace_dir

#: How many entries the brief names before summarising the rest. The brief is a
#: prompt, so an unbounded listing of a mature repository would crowd out the
#: planning instruction it is meant to ground.
MAX_LISTED_ENTRIES: Final[int] = 40

#: Said when the project has no files, whether or not a directory exists for it.
#: Worded so a planner cannot read it as "the inventory is unavailable".
EMPTY_WORKSPACE: Final[str] = (
    "nothing at all -- no file of this project has been written yet"
)

_GIT_DIR: Final[str] = ".git"


def _entry_names(tree: Path) -> list[str]:
    """Name every top-level entry of *tree*, directories marked with a slash.

    Returns:
        The entries, sorted, excluding the git metadata directory.
    """
    return sorted(
        f"{entry.name}/" if entry.is_dir() else entry.name
        for entry in tree.iterdir()
        if entry.name != _GIT_DIR
    )


def _summarise(names: list[str]) -> str:
    """Render *names* as the phrase the brief carries.

    Returns:
        The listing, truncated with a count of what it left out.
    """
    if not names:
        return EMPTY_WORKSPACE
    shown = names[:MAX_LISTED_ENTRIES]
    listing = ", ".join(shown)
    remainder = len(names) - len(shown)
    if remainder:
        return f"{listing}, and {remainder} more"
    return listing


async def describe_project_workspace(
    *,
    base_root: Path,
    project_id: NotBlankStr,
) -> str:
    """Describe what *project_id*'s workspace currently holds.

    Args:
        base_root: Root every project's workspace lives under.
        project_id: The project being planned for.

    Returns:
        A phrase naming the workspace's top-level entries, or
        :data:`EMPTY_WORKSPACE` when it holds no project files.

    Raises:
        WorkspaceSetupError: *project_id* carries a path separator, so the
            resolved directory would sit outside the projects root.
    """
    tree = project_workspace_dir(base_root, project_id)
    try:
        names = await asyncio.to_thread(_entry_names, tree)
    except OSError:
        # A workspace that cannot be read is one whose contents are unknown,
        # and the brief's whole job here is to stop the planner filling an
        # unknown with recall. Saying "nothing" is the answer that keeps it
        # planning construction rather than integration.
        return EMPTY_WORKSPACE
    return _summarise(names)


__all__ = ["EMPTY_WORKSPACE", "MAX_LISTED_ENTRIES", "describe_project_workspace"]
