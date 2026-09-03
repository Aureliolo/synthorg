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

import errno
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


def ensure_shared_dir(path: Path, *, within: Path | None = None) -> None:
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
        within: The tree the path was validated against, when there is one.
            A caller resolves and checks its path once, and a component
            below this root that is a symlink by the time it is walked was
            swapped in after that check, so it is refused rather than
            descended into: the sandbox shares the tree and can plant one.

    Raises:
        PermissionError: If a component strictly below *within* is a symlink.
    """
    if within is not None and supports_descriptor_walk() and within in path.parents:
        # Walked by descriptor, so a component swapped for a link after any
        # check is refused at the moment it is opened rather than descended
        # into; the path-based scan below is the platform fallback.
        os.close(open_shared_dir(path, within=within))
        return
    missing = [p for p in (path, *path.parents) if not p.exists()]
    if within is not None:
        _refuse_symlinks_below(path, within)
    for component in reversed(missing):
        try:
            component.mkdir()
        except FileExistsError:
            if within is not None and component.is_symlink():
                _refuse(component)
            # Placed between the scan and here, so somebody else owns its
            # mode. The scan stays as a pre-filter because the ancestors it
            # skips include the filesystem root, which is not ours to attempt.
            continue
        component.chmod(WORKSPACE_DIR_MODE)


#: ``O_NOFOLLOW`` where the platform has it; zero elsewhere, where the walk
#: that needs it is never taken (see :func:`supports_descriptor_walk`).
NO_FOLLOW_FLAG: Final[int] = getattr(  # lint-allow: ghost-attribute-read -- stdlib
    os, "O_NOFOLLOW", 0
)
_DIRECTORY_FLAG: Final[int] = getattr(  # lint-allow: ghost-attribute-read -- stdlib
    os, "O_DIRECTORY", 0
)


def supports_descriptor_walk() -> bool:
    """Whether this platform can walk and write a tree by descriptor.

    Returns:
        ``True`` where ``dir_fd`` and ``O_NOFOLLOW`` are both honoured, which
        is every POSIX platform the sandbox shares a tree on; Windows has
        neither, and there the path-based fallback is the live one.
    """
    return (
        os.open in os.supports_dir_fd
        and os.mkdir in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.replace in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
    )


def open_shared_dir(path: Path, *, within: Path, create: bool = True) -> int:
    """Open *path* below *within* by descriptor, creating what is missing.

    A path is re-resolved on every operation that names it, so a check made
    on one operation says nothing about the next: a sandbox sharing the tree
    can replace a checked directory with a link in between, and the write
    that follows lands wherever the link points. A descriptor pins the
    directory itself. The root is opened by path (a link at or above it is
    the operator's business), and every component below it is opened
    relative to its parent's descriptor with ``O_NOFOLLOW``, so a link
    anywhere below the root is refused at the moment it is reached, however
    recently it was planted, and a directory created here is created inside
    the parent that was opened rather than at a path that may since have
    changed.

    Args:
        path: The directory to open; must sit strictly below *within*.
        within: The tree the path was validated against.
        create: Create missing components under the sharing contract, or
            refuse a missing one as the filesystem does.

    Returns:
        An open directory descriptor for *path*; the caller closes it.

    Raises:
        PermissionError: If a component below *within* is a symlink.
        FileNotFoundError: If a component is missing and *create* is false.
        OSError: For any other failure to open or create a component.
    """
    parts = path.relative_to(within).parts
    fd = os.open(within, os.O_RDONLY | _DIRECTORY_FLAG)
    try:
        for index, name in enumerate(parts):
            component = within.joinpath(*parts[: index + 1])
            child = _descend(fd, name, component, create=create)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def _descend(parent_fd: int, name: str, component: Path, *, create: bool) -> int:
    """Open *name* under *parent_fd* without following a link, making it if asked.

    Returns:
        The child's descriptor.

    Raises:
        PermissionError: If *name* is a symlink.
        FileNotFoundError: If *name* is missing and *create* is false.
    """
    flags = os.O_RDONLY | _DIRECTORY_FLAG | NO_FOLLOW_FLAG
    created = False
    try:
        return _open_no_follow(parent_fd, name, flags, component)
    except FileNotFoundError:
        if not create:
            raise
    try:
        os.mkdir(name, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        # Placed between the failed open and here, so somebody else owns its
        # mode; opened below, where a link is refused like any other.
        pass
    child = _open_no_follow(parent_fd, name, flags, component)
    if created:
        # Present wherever this walk runs; the platform gate above is what
        # keeps a platform without it off this path.
        fchmod = getattr(  # lint-allow: ghost-attribute-read -- stdlib
            os, "fchmod", None
        )
        if fchmod is not None:
            fchmod(child, WORKSPACE_DIR_MODE)
    return child


def _open_no_follow(parent_fd: int, name: str, flags: int, component: Path) -> int:
    """Open *name* under *parent_fd*, refusing a symlink as the sharing rule does.

    Returns:
        The descriptor.

    Raises:
        PermissionError: If *name* is a symlink.
        OSError: For any other failure to open it, including its absence.
    """
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        # ``O_NOFOLLOW`` on a link is ELOOP on Linux and EMLINK on the BSDs.
        if exc.errno in {errno.ELOOP, errno.EMLINK}:
            _refuse(component)
        raise


def _refuse_symlinks_below(path: Path, within: Path) -> None:
    """Refuse a path any of whose components below *within* is a symlink.

    Raises:
        PermissionError: If such a component exists.
    """
    for component in (path, *path.parents):
        if component == within or within not in component.parents:
            break
        if component.is_symlink():
            _refuse(component)


def _refuse(component: Path) -> None:
    """Raise the refusal for a symlinked *component*.

    Raises:
        PermissionError: Always.
    """
    msg = f"refusing to descend into a symlink inside the workspace: {component}"
    raise PermissionError(msg)


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
