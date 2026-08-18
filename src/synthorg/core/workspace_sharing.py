# module-kind: code
"""The contract under which the backend and a sandbox share one workspace.

An agent writes its code through the backend process and then runs a test
against that code inside a sandbox container. The two are different POSIX
identities on purpose: the sandbox is the confinement boundary, so it must
not be able to act as the process that governs it. That leaves exactly one
way for a test runner to open the source it is pointed at, which is a group
both identities hold.

The backend's own gid IS that group. Deriving it rather than configuring it
is deliberate: a configured value would be a second owner for a fact the
operating system already holds, and the failure mode of a stale one is
silent, returning the sandbox to the no-access state this module exists to
end. The sandbox joins it as a supplementary gid at container creation, so
an operator's own devcontainer image needs nothing baked in to take part.

The modes are stated for the same reason they are shared. ``mkstemp``
creates owner-only because it is a private-temp-file primitive, and an
ordinary umask strips the group-write bit from anything created without
one, so neither the primitive an atomic write uses nor the ambient default
produces a file the group can reach. Nothing here depends on the umask
being any particular value: every mode below is applied explicitly after
creation rather than requested at it, precisely so the ambient one cannot
decide the outcome. Nothing is granted to *other*: the group IS the sharing
mechanism, so a world bit would widen reach without serving it. Directories
carry setgid so a file the sandbox creates lands under the shared group
rather than its own, which is what lets the backend read build output back.
"""

import os
import stat
from pathlib import Path
from typing import Final

#: Owner and group read/write. Execute is absent because a writer cannot
#: know a file is a program; a script that needs the bit keeps it, since
#: :func:`delivered_file_mode` never narrows what a file already grants.
WORKSPACE_FILE_MODE: Final[int] = 0o660

#: Group-writable and setgid. Group-write on the *directory* is what lets an
#: in-place rewrite land, because an atomic replace needs the directory
#: entry rather than the file. Setgid makes every entry created under it
#: inherit the shared group instead of the creator's primary one.
WORKSPACE_DIR_MODE: Final[int] = 0o2770

#: Distance between the owner triad and the group triad in a POSIX mode.
_OWNER_TO_GROUP_SHIFT: Final[int] = 3

#: Withholds nothing from the group and everything from *other*, so a file
#: created by a program we spawned rather than by this code still lands
#: shareable. All three *other* bits, not write alone: the sharing contract
#: names the group, so read and execute are as much of a grant as write is,
#: and the explicit modes above already deny the whole triad.
SHARED_UMASK: Final[int] = 0o007


def apply_shared_umask() -> int:
    """Set the process umask so spawned programs create shareable files.

    Every mode above is applied explicitly after creation, so this changes
    nothing about the files this code writes. It exists for the files it
    does NOT write: a subprocess creating something inside the shared tree
    is reached by no rule here, and the ambient umask is the only lever
    over it.

    Git is the case that proved it. ``core.sharedRepository=group`` covers
    the files git itself manages, and ``COMMIT_EDITMSG`` is not one of
    them: git writes it raw, under the umask. So whichever identity
    committed first left it at ``0644``, and the other's ``git commit``
    failed with ``could not open '.git/COMMIT_EDITMSG': Permission
    denied`` for the life of the workspace. A live run lost a task to it,
    the agent having already run its suite green.

    Process-wide because a umask is process-wide; scoping it around a
    spawn would race every other thread in the same interpreter. Nothing
    is granted to *other*, so the reach is exactly the group that already
    shares the tree.

    Returns:
        The umask that was in effect before this call.
    """
    return os.umask(SHARED_UMASK)


def workspace_share_gid() -> int | None:
    """Return the gid the sandbox must join to reach the workspace.

    Returns:
        The running process's group id, or ``None`` where the platform has
        no POSIX groups and the question does not arise.
    """
    # POSIX-only; Windows has no groups, so the absent branch is live there.
    getgid = getattr(os, "getgid", None)  # lint-allow: ghost-attribute-read -- stdlib
    if getgid is None:
        return None
    return int(getgid())


def ensure_shared_dir(path: Path) -> None:
    """Create *path* and its missing ancestors under the sharing contract.

    Each component is created separately because ``mkdir(parents=True)``
    does not report which ones it made, and re-moding a directory an
    operator or a checkout already placed would turn an ordinary write into
    a permissions change nobody asked for. Only what THIS call created is
    re-moded, and that is decided by ``mkdir`` raising rather than by a
    prior ``exists()``: between the check and the create, a peer or the
    sandbox can place the same directory, and a check-then-act would then
    apply our mode to somebody else's.

    The mode is applied after creation rather than passed to ``mkdir``,
    whose *mode* argument the process umask masks: a umask of ``022`` drops
    exactly the group-write bit the sandbox needs.

    Args:
        path: The directory to ensure exists.
    """
    missing = [p for p in (path, *path.parents) if not p.exists()]
    for component in reversed(missing):
        try:
            component.mkdir()
        except FileExistsError:
            # Placed between the scan and here, so somebody else owns its
            # mode. The scan stays as a pre-filter because the ancestors it
            # skips include the filesystem root, which is not ours to attempt.
            continue
        component.chmod(WORKSPACE_DIR_MODE)


def delivered_file_mode(current_mode: int | None) -> int:
    """Return the mode a workspace file should carry after a write.

    A file the group can already read keeps exactly the mode it has, so
    overwriting an executable script leaves it executable and a file
    deliberately widened by a build is not narrowed back. A file the group
    cannot read is granted whatever the owner holds, which both repairs the
    owner-only files written before this contract existed and preserves an
    execute bit while doing it.

    Args:
        current_mode: The target's current permission bits, or ``None`` when
            the write is creating the file.

    Returns:
        The permission bits to apply to the delivered file.
    """
    if current_mode is None:
        return WORKSPACE_FILE_MODE
    mode = stat.S_IMODE(current_mode)
    if mode & stat.S_IRGRP:
        return mode
    return mode | (mode & stat.S_IRWXU) >> _OWNER_TO_GROUP_SHIFT
