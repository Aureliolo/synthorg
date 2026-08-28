# module-kind: code
"""Did this run change the tree at all?

The narrower question, "are the declared artifacts there", is asked by
:mod:`.expected_artifact_check` and is the right one for judging whether a
run kept its promise. It is the wrong one for judging whether a run did
anything, because a declaration is written by the planner before the tree
exists, from a title and a sentence. A run briefed to build the CSV reader
that writes ``sqlcsv/reader.py`` where ``sqlcsv/csv_reader.py`` was declared
has produced eight modules and satisfies no declaration.

So this asks the workspace instead of the plan. A fingerprint is every file
under the root with a key for its content, and a run that leaves that set
identical produced nothing: no file appeared, none was removed, and none was
rewritten. Content rather than length because the verdict this drives is
whether to FAIL the task, and an edit that keeps a file's size (a flipped
constant, a corrected identifier, a rewritten line) is ordinary work that a
byte count cannot see. The tree walked here is pruned of everything a tool
writes, so what remains is authored source and hashing it is milliseconds
against a run that took minutes.

Nothing is ever opened through a link. ``Path.is_file`` follows one, so a
workspace an agent can write is a workspace that can hold a symlink to a
character device, and hashing ``/dev/zero`` never reaches EOF: the thread
this runs on would never return. A link is keyed by its own text, which is
also the honest answer, since what the run authored is the link rather than
whatever it points at. Anything else that is not a regular file is keyed by
its kind for the same reason, because opening a FIFO blocks until somebody
writes to it.

A tree a tool writes is pruned wherever it appears, because an agent that
ran the suite it was given produced a ``__pycache__`` and nothing else, and
reading that as delivery would wave through exactly the run this exists to
catch. Installed dependency trees are in the same set: fetching them is not
authoring, and a workspace holding one is otherwise most of what gets walked.
A task whose deliverable genuinely is one of these has declared it, and the
declared-artifact check is what answers for it.

Everything else is the caller's to exclude, by name, among the root's own
children: a harness mounting inputs into the tree it grades knows which of
them it put there, and this module does not.
"""

import builtins
import hashlib
import stat
from collections.abc import Collection
from pathlib import Path
from typing import Final

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
)

logger = get_logger(__name__)

#: Every file under a workspace root, as ``(posix relative path, content
#: key)``. A key is a hex digest for a regular file and a marker for
#: everything else, so no two of them can collide.
type WorkspaceFingerprint = frozenset[tuple[str, str]]

#: Directories written by a tool rather than by an author, at any depth. None
#: of them is ever evidence that a run delivered.
_GENERATED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
    }
)

#: Recorded for a file that is there but cannot be read. It keeps the path in
#: the fingerprint, because a file the filesystem refuses to answer for is
#: still a file the run produced, and dropping it would read as an absence.
_UNREADABLE: Final[str] = "<unreadable>"

#: Marks a symlink, whose key is its link text rather than its target's
#: content. Prefixed so link text cannot be mistaken for a digest.
_SYMLINK_PREFIX: Final[str] = "<symlink>"

#: Marks anything else that is not a regular file, keyed by its kind because
#: its contents cannot be read without blocking.
_SPECIAL_PREFIX: Final[str] = "<special>"


def fingerprint_tree(
    root: Path, *, exclude: Collection[str] = ()
) -> WorkspaceFingerprint:
    """Fingerprint every file under *root*.

    Args:
        root: The workspace directory. It need not exist: an unprovisioned
            workspace holds nothing.
        exclude: Names of *root*'s own children to leave out. Applied at the
            top level only, so a caller excluding ``README.md`` still counts
            one a run wrote inside a package it created.

    Returns:
        The set of ``(relative path, content key)`` pairs, empty when *root*
        is not a directory.
    """
    if not root.is_dir():
        return frozenset()
    excluded = frozenset(exclude)
    entries: list[tuple[str, str]] = []
    for parent, directories, files in root.walk(on_error=_walk_failed):
        at_root = parent == root
        # Assigning through the slice is what prunes the walk: ``Path.walk``
        # reads this same list to decide where it descends next, so rebinding
        # the name would leave the subtree walked and the pruning inert.
        directories[:] = [
            name
            for name in directories
            if name not in _GENERATED_DIRS and not (at_root and name in excluded)
        ]
        relative = parent.relative_to(root)
        # A symlinked directory is listed here and never descended into,
        # because the walk does not follow links. Recorded anyway, or a run
        # whose whole output was a link would read as having produced nothing.
        entries.extend(
            ((relative / name).as_posix(), _content_key(parent / name))
            for name in directories
            if (parent / name).is_symlink()
        )
        for name in files:
            if at_root and name in excluded:
                continue
            entries.append(((relative / name).as_posix(), _content_key(parent / name)))
    return frozenset(entries)


def _walk_failed(exc: OSError) -> None:
    """Report a subtree the walk could not enter.

    ``Path.walk`` discards its own :class:`OSError` unless handed this, so a
    directory the filesystem refuses simply vanishes from the fingerprint. The
    verdict this fingerprint drives is that a run produced nothing anywhere,
    so a silently shrunk walk can fail delivered work with nothing in the log
    to say the tree was never fully read. Reported rather than raised, on the
    same rule as :func:`_content_key`: one unreadable subtree must not cost
    the answer for everything beside it.
    """
    logger.warning(
        EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
        phase="fingerprint_walk",
        subtree=str(exc.filename or ""),
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )


def _content_key(path: Path) -> str:
    """Identify *path*'s content, without ever reading through a link.

    Returns:
        A hex digest for a regular file, the link text for a symlink, the
        kind for anything else, or :data:`_UNREADABLE` when reading it fails.
        One unreadable file must not cost the fingerprint every other file
        beside it, which is what an escaping exception would do. Broader than
        ``OSError`` because the failure modes are not all one class: a path
        the OS accepted but Python cannot render raises ``ValueError``, and a
        fingerprint that dies on one such entry answers for none of the tree.

    Raises:
        MemoryError: Re-raised: the process is in no state to keep walking.
        RecursionError: Re-raised, on the same rule.
    """
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            # Rendered posix-style so a key means the same thing whichever
            # platform took the fingerprint, as every path in it already does.
            return f"{_SYMLINK_PREFIX}{path.readlink().as_posix()}"
        if not stat.S_ISREG(mode):
            return f"{_SPECIAL_PREFIX}{stat.S_IFMT(mode)}"
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except builtins.MemoryError, RecursionError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised above
        # lint-allow: swallow-ok -- one entry's failure must not decide the
        # tree's answer, and the marker returned below is not a silent drop:
        # it keeps the path in the fingerprint, so the file still counts as
        # produced and only its content goes unread. Raising here would let a
        # single unreadable entry fail a run that delivered everything else.
        logger.warning(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            phase="fingerprint",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _UNREADABLE


__all__ = ["WorkspaceFingerprint", "fingerprint_tree"]
