# module-kind: code
"""Which tool categories may write the workspace they are handed.

The workspace is read-only by default, which is right for a tool that only
needs to look at the project: a web fetch or a database query has no reason
to be able to change it, and read-only is the cheapest way to say so.

Some categories do have a reason, and the test is whether the tool's own
output lands in the workspace. A build writes objects and lock files, a shell
command writes whatever it was asked to write, git writes its own directory,
and a browser or desktop check writes the screenshot that IS its result;
refusing those makes the tools decorative rather than confined.

The distinction is declared as a set rather than resolved from the tool's
declared action type, because a category is what the sandbox is handed and
inferring the answer from something else would put the decision in two
places.
"""

from enum import StrEnum
from typing import Final

from synthorg.security.autonomy.enums import ToolCategory


class MountMode(StrEnum):
    """The modes a workspace bind may be created under.

    One domain rather than three, because the set is load-bearing in a place
    that is easy to miss: a container is keyed by its mode and torn down by
    sweeping every member, so a mode that exists for creation but not for the
    sweep leaks a container until the process exits. The values are the
    spellings Docker takes in a bind string, so a member interpolates
    directly.
    """

    READ_WRITE = "rw"
    READ_ONLY = "ro"


#: Read-write is granted here and nowhere else. Membership is a claim that a
#: category's tools legitimately modify the project, not that they are
#: trusted: the confinement is the container, the dropped capabilities and
#: the separate uid, none of which this widens.
WRITABLE_WORKSPACE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        ToolCategory.BROWSER.value,
        ToolCategory.CODE_EXECUTION.value,
        ToolCategory.DESKTOP.value,
        ToolCategory.TERMINAL.value,
        ToolCategory.VERSION_CONTROL.value,
    }
)

#: Every mode a container may be created under, derived from the enum rather
#: than restated, so the teardown sweep cannot fall behind what creation
#: admits.
MOUNT_MODES: Final[tuple[MountMode, ...]] = tuple(MountMode)


def resolve_mount_mode(category: str, configured: MountMode) -> MountMode:
    """Return the workspace mount mode for a sandboxed *category*.

    Args:
        category: The :class:`ToolCategory` value the sandbox was resolved
            for. An empty string means no category was supplied, which is
            answered with the configured default.
        configured: The operator's ``mount_mode``, applied to every category
            that does not write.

    Returns:
        The mode the workspace bind is created under.
    """
    if category in WRITABLE_WORKSPACE_CATEGORIES:
        return MountMode.READ_WRITE
    return configured
