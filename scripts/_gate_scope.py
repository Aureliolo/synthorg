#!/usr/bin/env python3
"""Shared path-scoping helper for the gates that accept ``--files``.

Several gates can decide their verdict for one file from that file alone, so
they take a ``--files`` list and the agent-time PostToolUse dispatcher
(``run_edit_time_gates.py``) uses it to narrow a scan to whatever was just
edited. Each of those gates needs the same primitive first: turn a list of
caller-supplied path strings into the subset that exists, carries an
interesting suffix, and sits under one of the gate's own scan roots.

Written once here rather than per gate because the four copies had already
begun to diverge in ways that change behaviour at the margins: two deduped
inside the loop and two afterwards, and they sorted on different keys, so the
order in which violations were reported depended on which gate found them.

A gate still owns its own scope decision. This function takes the roots and
suffixes explicitly on every call and holds no state, so it stays a pure
function of its arguments: the gates share the mechanics, never the policy.
That also keeps it safe for the consolidated pre-push runner, which fans the
gates across a pool of reused workers and requires each to be stateless with
respect to its siblings.

Out-of-scope paths are dropped rather than rejected. The dispatcher hands over
whatever a developer edited, so deciding what is interesting belongs to the
gate, not to the call site. The caller learns how many were dropped from
``SelectionResult.skipped`` and can say so, which is what stops a silently
mismatched routing table from reading as a clean scan.
"""

import dataclasses
from collections.abc import Sequence
from pathlib import Path


@dataclasses.dataclass(frozen=True, slots=True)
class ScopedFile:
    """One in-scope file selected from a caller-supplied path list.

    Attributes:
        path: Absolute, resolved path to the file.
        rel: Repo-relative POSIX path, the form gates report violations against.
        root: The scan root this file matched, absolute. Gates whose assertions
            differ per root (``src/synthorg/`` versus ``tests/``) derive that
            from here rather than re-deriving it from the path.
    """

    path: Path
    rel: str
    root: Path


@dataclasses.dataclass(frozen=True, slots=True)
class SelectionResult:
    """The in-scope subset of a caller-supplied path list, plus what was cut.

    Attributes:
        selected: In-scope files, ordered by ``rel`` so a gate's output is
            deterministic regardless of the order paths arrived in.
        skipped: Count of supplied paths that matched no root, carried no
            interesting suffix, or did not resolve to a file.
    """

    selected: tuple[ScopedFile, ...]
    skipped: int


def _resolve_root(root: Path, project_root: Path) -> Path | None:
    """Return *root* as an absolute path inside *project_root*, or ``None``.

    Resolves before the containment check so a symlinked root cannot smuggle
    in a tree outside the repository.

    Args:
        root: Scan root, absolute or relative to *project_root*.
        project_root: Resolved repository root.

    Returns:
        The resolved root, or ``None`` if it escapes *project_root*.
    """
    try:
        resolved = (project_root / root).resolve()
    except OSError:
        return None
    return resolved if resolved.is_relative_to(project_root) else None


def select_scoped_files(
    files: Sequence[str],
    *,
    project_root: Path,
    roots: Sequence[Path],
    suffixes: frozenset[str],
) -> SelectionResult:
    """Return the in-scope subset of *files*.

    ``project_root / candidate`` needs no ``is_absolute`` branch: joining a
    path with an absolute right-hand operand already discards the left one, on
    POSIX and on Windows (including across drive letters and UNC roots).

    Resolving before the containment check is what makes the check meaningful:
    a symlink under a scan root pointing outside the repository resolves to its
    real target first, so it fails ``is_relative_to`` and is dropped instead of
    being scanned as though it were in-tree.

    Args:
        files: Caller-supplied path strings, absolute or repo-relative.
        project_root: Resolved repository root.
        roots: Scan roots, absolute or relative to *project_root*.
        suffixes: File extensions this gate reads, e.g. ``{".py"}``.

    Returns:
        The selection, with a count of everything dropped.
    """
    # Resolved once, up front: every containment check below compares a
    # RESOLVED path against this, and comparing against an unresolved root
    # silently fails wherever the root contains a symlink or junction. A
    # Windows temp directory is normally reached through one, so a caller
    # passing such a root would select nothing and report a clean scan.
    try:
        root_anchor = project_root.resolve()
    except OSError:
        return SelectionResult(selected=(), skipped=len(files))
    resolved_roots = [
        resolved
        for resolved in (_resolve_root(root, root_anchor) for root in roots)
        if resolved is not None
    ]
    by_rel: dict[str, ScopedFile] = {}
    skipped = 0
    for raw in files:
        try:
            resolved_path = (root_anchor / Path(raw)).resolve()
        except OSError:
            # An embedded NUL, a reserved Windows device name, or an
            # unreachable network path. Not a file this gate can have an
            # opinion about, and not a reason to abort the whole scan.
            skipped += 1
            continue
        matched = next(
            (root for root in resolved_roots if resolved_path.is_relative_to(root)),
            None,
        )
        if matched is None or resolved_path.suffix not in suffixes:
            skipped += 1
            continue
        if not resolved_path.is_file():
            # Covers a deleted path and a directory passed where a file was
            # meant; both are ordinary for an edit-time caller.
            skipped += 1
            continue
        rel = resolved_path.relative_to(root_anchor).as_posix()
        if rel not in by_rel:
            by_rel[rel] = ScopedFile(path=resolved_path, rel=rel, root=matched)
    selected = tuple(by_rel[rel] for rel in sorted(by_rel))
    return SelectionResult(selected=selected, skipped=skipped)
