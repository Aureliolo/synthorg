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
nothing there, so absence is spelled out rather than left to silence. A
workspace that EXISTS but cannot be listed is a third state and gets its own
wording: reporting it as empty would be the same false premise arriving by a
different route, and this time asserted by us rather than recalled.

Entry names are agent-authored. The file tools root at this very directory and
validate containment alone, so a name may carry newlines and angle brackets;
each is flattened and length-capped here, and the caller fences the result.

``.git`` is excluded deliberately: every provisioned workspace has one, and
naming it in a list of what the project holds reads as work already done.
"""

import asyncio
from pathlib import Path
from typing import Final

from synthorg.core.types import NotBlankStr, flatten_label
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.observability import get_logger
from synthorg.observability.events.workspace import PROJECT_WORKSPACE_UNREADABLE
from synthorg.observability.redaction import safe_error_description

logger = get_logger(__name__)

#: How many entries the brief names before summarising the rest. The brief is a
#: prompt, so an unbounded listing of a mature repository would crowd out the
#: planning instruction it is meant to ground.
MAX_LISTED_ENTRIES: Final[int] = 40

#: Longest an entry name may be before it is elided. A crafted name is bounded
#: by the filesystem's own limit, not by anything reasonable for a prompt.
MAX_NAME_CHARS: Final[int] = 120

#: Said when the project has no files, whether or not a directory exists for it.
#: Worded so a planner cannot read it as "the inventory is unavailable".
EMPTY_WORKSPACE: Final[str] = (
    "nothing at all -- no file of this project has been written yet"
)

#: Said when the directory is there but could not be read. Deliberately NOT a
#: statement about contents: the planner must neither assume files exist nor
#: assume they do not, and the prohibition above it already covers the first.
UNREADABLE_WORKSPACE: Final[str] = (
    "unknown -- the workspace could not be read, so treat its contents as "
    "undetermined rather than empty"
)

_GIT_DIR: Final[str] = ".git"


def _safe_name(name: str, *, is_dir: bool) -> str:
    """Render one entry name safely enough to interpolate.

    Returns:
        The name flattened to a single line without angle brackets, elided
        past :data:`MAX_NAME_CHARS`, with directories marked by a slash.
    """
    flat = flatten_label(name)
    if len(flat) > MAX_NAME_CHARS:
        flat = f"{flat[:MAX_NAME_CHARS]}..."
    return f"{flat}/" if is_dir else flat


def _entry_names(tree: Path) -> list[str]:
    """Name every top-level entry of *tree*, directories marked with a slash.

    Returns:
        The entries, sorted, excluding the git metadata directory.
    """
    return sorted(
        _safe_name(entry.name, is_dir=entry.is_dir())
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
        A phrase naming the workspace's top-level entries,
        :data:`EMPTY_WORKSPACE` when it holds no project files, or
        :data:`UNREADABLE_WORKSPACE` when a directory is there but listing it
        failed.

    Raises:
        WorkspaceSetupError: *project_id* carries a path separator, so the
            resolved directory would sit outside the projects root.
    """
    tree = project_workspace_dir(base_root, project_id)
    if not await asyncio.to_thread(tree.is_dir):
        return EMPTY_WORKSPACE
    try:
        names = await asyncio.to_thread(_entry_names, tree)
    except OSError as exc:
        # The directory is there, so "nothing has been written yet" would be a
        # false statement of fact rather than a description of an absence, and
        # it is the same false premise this module exists to prevent. A read
        # that failed is reported as undetermined, and logged, because nothing
        # else would explain a plan written blind.
        logger.warning(
            PROJECT_WORKSPACE_UNREADABLE,
            project_id=project_id,
            workspace_path=str(tree),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return UNREADABLE_WORKSPACE
    return _summarise(names)


__all__ = [
    "EMPTY_WORKSPACE",
    "MAX_LISTED_ENTRIES",
    "MAX_NAME_CHARS",
    "UNREADABLE_WORKSPACE",
    "describe_project_workspace",
]
