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
under the root with its size, and a run that leaves that set identical
produced nothing: no file appeared, none was removed, and none changed
length. Length rather than content because this decides whether to spend one
more turn, not whether work is correct, and hashing a whole tree twice a run
buys nothing that decision can use.

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

from collections.abc import Collection
from pathlib import Path
from typing import Final

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
)

logger = get_logger(__name__)

#: Every file under a workspace root, as ``(posix relative path, size)``.
type WorkspaceFingerprint = frozenset[tuple[str, int]]

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

#: Size recorded for a file that is there but cannot be measured. It keeps
#: the path in the fingerprint, because a file the filesystem refuses to
#: answer for is still a file the run produced, and dropping it would read as
#: an absence.
_UNREADABLE: Final[int] = -1


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
        The set of ``(relative path, size)`` pairs, empty when *root* is not
        a directory.
    """
    if not root.is_dir():
        return frozenset()
    excluded = frozenset(exclude)
    entries: list[tuple[str, int]] = []
    for parent, directories, files in root.walk():
        at_root = parent == root
        directories[:] = [
            name
            for name in directories
            if name not in _GENERATED_DIRS and not (at_root and name in excluded)
        ]
        relative = parent.relative_to(root)
        for name in files:
            if at_root and name in excluded:
                continue
            entries.append(((relative / name).as_posix(), _size_of(parent / name)))
    return frozenset(entries)


def _size_of(path: Path) -> int:
    """Measure *path*, degrading rather than losing the whole answer.

    Returns:
        The file's size, or :data:`_UNREADABLE` when the filesystem refuses.
        One unreadable file must not cost the fingerprint every other file
        beside it, which is what an escaping ``OSError`` would do.
    """
    try:
        return path.stat().st_size
    except OSError as exc:
        logger.warning(
            EXECUTION_ENGINE_ARTIFACT_PROBE_DEGRADED,
            phase="fingerprint",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return _UNREADABLE


__all__ = ["WorkspaceFingerprint", "fingerprint_tree"]
