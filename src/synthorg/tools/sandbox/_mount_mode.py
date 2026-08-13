# module-kind: code
"""Which tool categories may write the workspace they are handed.

The workspace is read-only by default, which is right for a tool that only
needs to look at the project: a web fetch or a database query has no reason
to be able to change it, and read-only is the cheapest way to say so.

Three categories do have a reason. A build writes objects and lock files, a
shell command writes whatever it was asked to write, and git writes its own
directory; refusing those makes the tools decorative rather than confined.
The distinction is declared as a set rather than resolved from the tool's
declared action type, because a category is what the sandbox is handed and
inferring the answer from something else would put the decision in two
places.
"""

from typing import Final

from synthorg.security.autonomy.enums import ToolCategory

#: Read-write is granted here and nowhere else. Membership is a claim that a
#: category's tools legitimately modify the project, not that they are
#: trusted: the confinement is the container, the dropped capabilities and
#: the separate uid, none of which this widens.
WRITABLE_WORKSPACE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        ToolCategory.CODE_EXECUTION.value,
        ToolCategory.TERMINAL.value,
        ToolCategory.VERSION_CONTROL.value,
    }
)

_READ_WRITE: Final[str] = "rw"

#: Every mode a container may be created under. A reused container is keyed
#: by its mode, so this is also the set an owner's teardown must sweep.
MOUNT_MODES: Final[tuple[str, ...]] = ("rw", "ro")


def resolve_mount_mode(category: str, configured: str) -> str:
    """Return the workspace mount mode for a sandboxed *category*.

    Args:
        category: The :class:`ToolCategory` value the sandbox was resolved
            for. An empty string means no category was supplied, which is
            answered with the configured default.
        configured: The operator's ``mount_mode``, applied to every category
            that does not write.

    Returns:
        ``"rw"`` or ``"ro"``.
    """
    if category in WRITABLE_WORKSPACE_CATEGORIES:
        return _READ_WRITE
    return configured
