# module-kind: code
"""Read the files a task declared, so a reviewer judges the deliverable.

The completion oracle's peer reviewer is the most load-bearing gate in the
chain: fail-closed, on by default, reviewing every task. Reading only the
agent's closing message would let an APPROVE verdict mean "wrote a convincing
summary" rather than "the work is there". This reads what the task promised:
the files at its declared paths.

The report is JSON, not delimited text. Path, status and content occupy
separate slots, so a file cannot spell a second artifact, a forged
"further artifacts omitted" note, or a second closing message inside its own
body and have the reviewer read it as structure. Delimiter-formatted output
would make the evidence forgeable by the very content it is evidence about.

Content is bounded twice, per file and in total, because a reviewer prompt is
a fixed budget and one large generated file would otherwise crowd out every
other deliverable. Each file is read up to its bound rather than read whole
and sliced, so a declared path pointing at something enormous cannot exhaust
memory before the bound applies. Truncation and omission are reported in the
document rather than left silent.

Only a relative, path-shaped declaration is read, and only from inside the
project workspace. An absolute declaration is never opened: the read runs in
the backend process, not the sandbox, so honouring one would hand any file
that process can reach to an external model.

When no declared path came back at all, the workspace's own files are read
in their place. A declaration is a guess written before the tree exists, so
a run that solved the task under other names satisfies none of them and is
sent to review precisely so a reviewer can judge the substitution; handing
that reviewer an empty section leaves it approving on the closing message,
which is the reading this module exists to stop.

The files are agent-written and therefore untrusted: whatever the reviewer
receives is fenced by the caller before it reaches a prompt.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Final

from pydantic import JsonValue

from synthorg.core.artifact import ExpectedArtifact
from synthorg.engine.artifacts.expected_artifact_check import is_probeable_path
from synthorg.engine.artifacts.workspace_fingerprint import fingerprint_tree
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.deliverable import DELIVERABLE_READ_FAILED
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.kill_switch import resolve_int_with_fallback
from synthorg.settings.resolver_protocol import ConfigResolverProtocol

logger = get_logger(__name__)

#: Bounds used when no resolver is wired. They mirror the setting defaults,
#: which are the authority; these keep a resolverless harness reviewing real
#: content rather than nothing.
_FALLBACK_MAX_CHARS_PER_FILE: Final[int] = 20000
_FALLBACK_MAX_CHARS_TOTAL: Final[int] = 60000

_MAX_PER_FILE_KEY: Final[str] = "review_artifact_max_chars_per_file"
_MAX_TOTAL_KEY: Final[str] = "review_artifact_max_chars_total"

#: Longest path label carried into the document. A declaration is planner
#: free text with no length bound of its own, and the label is charged
#: against the same budget as content, so it needs its own ceiling.
_MAX_PATH_LABEL: Final[int] = 256

#: What :func:`json.dumps` writes between two elements of a list. Charged per
#: entry after the first: the wrapper is costed against an EMPTY list, so a
#: separator is rendered into the section that nothing ever budgeted for, and
#: the overrun grows with the entry count.
_SEPARATOR_BYTES: Final[int] = len(", ")

#: What the reader found at a declared path. ``not_a_path`` is a prose
#: deliverable rather than a file, which the reviewer can still judge; it is
#: reported so the reviewer knows the declaration was not simply missed.
#: Public, because a consumer that wants only the files that genuinely came
#: back has to recognise this status, and a second spelling of the word would
#: silently select nothing.
ARTIFACT_STATUS_READ: Final[str] = "read"
_STATUS_ABSENT: Final[str] = "not_produced"
_STATUS_DIRECTORY: Final[str] = "directory"
_STATUS_UNREADABLE: Final[str] = "unreadable"
_STATUS_NOT_A_PATH: Final[str] = "not_a_path"
_STATUS_OMITTED: Final[str] = "omitted_for_budget"

#: Resolves ``(project_id, expected) -> the artifacts section``, or ``None``
#: when the task declared nothing. A mapping rather than serialised text: the
#: caller merges it with the agent's closing message and serialises once, so
#: the document is never parsed back out of its own rendering. Async because
#: the bounds are operator settings read live per review, so a retune arms
#: the next review rather than the next boot.
type DeliverableReader = Callable[
    [str, Sequence[ExpectedArtifact]], Awaitable[Mapping[str, JsonValue] | None]
]


def _read_one(
    declared: str, *, root: Path, limit: int, is_declaration: bool = True
) -> dict[str, JsonValue]:
    """Read one artifact, bounded at *limit* characters.

    Args:
        declared: The path to read.
        root: The resolved workspace directory.
        limit: Per-file content bound, in characters.
        is_declaration: Whether *declared* is a PLANNER's free text, which may
            be prose rather than a path. A name walked off the filesystem is
            already known to be a file, and putting it through the prose test
            would report a real produced file called ``design notes.md`` as
            "not a path". Containment is checked either way, because that is
            about where the file sits rather than how its name was obtained.

    Returns:
        Its entry in the document: always a ``path`` and a ``status``, plus
        ``content`` and ``truncated`` when something was read. A status is
        content too: "not_produced" is exactly what a reviewer needs to see,
        and hiding it would leave the reviewer judging a deliverable it does
        not know is missing.
    """
    label = declared[:_MAX_PATH_LABEL]
    if is_declaration and not is_probeable_path(declared):
        return {"path": label, "status": _STATUS_NOT_A_PATH}
    resolved = (root / Path(declared)).resolve()
    if resolved == root:
        # ``.`` and ``src/..`` name the workspace itself, which exists
        # whenever the run had one. Reading it as a deliverable would let an
        # empty run present the workspace as its output.
        return {"path": label, "status": _STATUS_ABSENT}
    if not resolved.is_relative_to(root):
        # A path the run could not legitimately have written is not the
        # task's output, so it is reported as absent rather than read.
        return {"path": label, "status": _STATUS_ABSENT}
    if not resolved.exists():
        return {"path": label, "status": _STATUS_ABSENT}
    if resolved.is_dir():
        return {"path": label, "status": _STATUS_DIRECTORY}
    if not resolved.is_file():
        # A named pipe or a device is not a deliverable, and opening one
        # blocks until somebody writes to it, which nobody here will. The
        # workspace is a tree an agent can write, so it can hold one.
        return {
            "path": label,
            "status": _STATUS_UNREADABLE,
            "reason": "not a regular file",
        }
    try:
        with resolved.open(encoding="utf-8", errors="replace") as handle:
            text = handle.read(limit + 1)
    except OSError as exc:
        return {
            "path": label,
            "status": _STATUS_UNREADABLE,
            "reason": safe_error_description(exc),
        }
    truncated = len(text) > limit
    return {
        "path": label,
        "status": ARTIFACT_STATUS_READ,
        "truncated": truncated,
        "content": text[:limit],
    }


def read_declared_artifacts(
    expected: Sequence[ExpectedArtifact],
    *,
    workspace: Path,
    max_bytes_per_file: int,
    max_total_bytes: int,
) -> dict[str, JsonValue] | None:
    """Assemble the declared artifacts into one reviewable section.

    Args:
        expected: The artifacts the task declared it would produce.
        workspace: The project's workspace directory.
        max_bytes_per_file: Per-file content bound, in characters.
        max_total_bytes: Total bound across the whole section.

    Returns:
        A mapping naming every declaration and what was found at it, or
        ``None`` when the task declared nothing.

        The whole section fits ``max_total_bytes``, wrapper and omission
        marker included. Budgeting only the content would let a limit of a
        few characters still return a multi-kilobyte document, which is the
        opposite of what a bound on what reaches a prompt is for.
    """
    if not expected:
        return None
    root = workspace.resolve()
    declared_paths = {str(artifact.path) for artifact in expected}
    # The wrapper is charged before any entry, because it is rendered
    # whatever else fits and a budget that ignored it could be spent
    # entirely on entries and still overrun.
    entries, budget = _pack_entries(
        [str(artifact.path) for artifact in expected],
        root=root,
        limit=max_bytes_per_file,
        budget=max_total_bytes
        - len(json.dumps({"declared": len(expected), "artifacts": []})),
        is_declaration=True,
    )
    section: dict[str, JsonValue] = {
        "declared": len(expected),
        "artifacts": entries,
    }
    if any(
        isinstance(entry, dict) and entry.get("status") == ARTIFACT_STATUS_READ
        for entry in entries
    ):
        return section
    # The dumped stand-in costs its own two braces where the real rendering
    # costs the two bytes of the separator joining it to ``artifacts``, so the
    # charge is exact rather than approximate. Keeping the stand-in a whole
    # document is what makes it exact; charging the key alone would undercount.
    instead = _read_produced_instead(
        root,
        declared_paths,
        limit=max_bytes_per_file,
        budget=budget - len(json.dumps({"produced_instead": []})),
    )
    if instead:
        section["produced_instead"] = instead
    return section


def _read_produced_instead(
    root: Path,
    declared: Collection[str],
    *,
    limit: int,
    budget: int,
) -> list[JsonValue]:
    """Read what the workspace holds when no declaration came back.

    A declaration is written before the tree exists, so a run that solved
    the task under other names satisfies none of them. That run now reaches
    review rather than being failed on its declarations, and a reviewer
    handed nothing but the agent's closing message approves on the strength
    of prose, which is what this module exists to stop. So when no declared
    path was read, the reviewer is shown what IS there instead.

    Args:
        root: The resolved workspace directory.
        declared: The declared paths, so a file already reported is not
            reported twice under a second heading.
        limit: Per-file content bound, in characters.
        budget: What is left of the section's total bound.

    Returns:
        One entry per file read, in path order so two reviews of the same
        tree read the same, plus an omission marker when the budget ran out.
        Empty when the workspace holds nothing, which is the honest answer
        for a run that produced nothing at all.
    """
    produced = sorted(
        path for path, _ in fingerprint_tree(root) if path not in declared
    )
    entries, _remaining = _pack_entries(
        produced, root=root, limit=limit, budget=budget, is_declaration=False
    )
    return entries


def _pack_entries(
    paths: Sequence[str],
    *,
    root: Path,
    limit: int,
    budget: int,
    is_declaration: bool,
) -> tuple[list[JsonValue], int]:
    """Read *paths* into entries, stopping when the section's budget runs out.

    The one owner of how a section spends its bound, because both headings
    this module renders spend the same one and two copies of the arithmetic
    drift apart on the first correction made to only one of them.

    Args:
        paths: What to read, in the order it should be rendered.
        root: The resolved workspace directory.
        limit: Per-file content bound, in characters.
        budget: What is left of the section's total bound.
        is_declaration: Whether *paths* are planner free text rather than
            names walked off the filesystem. See :func:`_read_one`.

    Returns:
        The entries, plus what remains of *budget* after them, so a caller
        rendering a second heading charges it against what the first left.
    """
    entries: list[JsonValue] = []
    for index, path in enumerate(paths):
        entry = _read_one(
            path,
            root=root,
            limit=max(0, min(limit, budget)),
            is_declaration=is_declaration,
        )
        # Charge the rendered entry, not just its content: the path label is
        # planner free text and reaches the same prompt budget.
        separator = _SEPARATOR_BYTES if entries else 0
        cost = len(json.dumps(entry)) + separator
        if cost > budget:
            omission: dict[str, JsonValue] = {
                "status": _STATUS_OMITTED,
                "count": len(paths) - index,
            }
            # The marker is content too, so it only goes in if it fits. A
            # section that overran while announcing that it overran would be
            # the same defect wearing a label.
            marker_cost = len(json.dumps(omission)) + separator
            if marker_cost <= budget:
                entries.append(omission)
                budget -= marker_cost
            break
        budget -= cost
        entries.append(entry)
    return entries, budget


def workspace_deliverable_reader(
    base_root: Path,
    *,
    config_resolver: ConfigResolverProtocol | None = None,
) -> DeliverableReader:
    """Bind a :data:`DeliverableReader` to the shared workspace root.

    Args:
        base_root: Root every project's workspace lives under.
        config_resolver: Live source of the two content bounds, re-read
            per review. ``None`` uses the shipped defaults.

    Returns:
        A reader resolving each project's own workspace directory.
    """

    async def _read(
        project_id: str, expected: Sequence[ExpectedArtifact]
    ) -> Mapping[str, JsonValue] | None:
        """Read *project_id*'s declared artifacts.

        Returns:
            The artifacts section, or ``None`` when nothing was declared.
            A workspace that could not be read yields a section saying so
            rather than ``None``: the reviewer must be able to tell "could
            not verify" from "nothing was promised", since collapsing them
            is what would let a storage fault read as a clean review.
        """
        if not expected:
            return None
        per_file = await resolve_int_with_fallback(
            resolver=config_resolver,
            namespace=SettingNamespace.ENGINE,
            key=_MAX_PER_FILE_KEY,
            fallback=_FALLBACK_MAX_CHARS_PER_FILE,
        )
        total = await resolve_int_with_fallback(
            resolver=config_resolver,
            namespace=SettingNamespace.ENGINE,
            key=_MAX_TOTAL_KEY,
            fallback=_FALLBACK_MAX_CHARS_TOTAL,
        )
        try:
            return await asyncio.to_thread(
                read_declared_artifacts,
                expected,
                workspace=project_workspace_dir(base_root, project_id),
                max_bytes_per_file=per_file,
                max_total_bytes=total,
            )
        except OSError as exc:
            logger.error(
                DELIVERABLE_READ_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return {
                "declared": len(expected),
                "workspace_error": "the project workspace could not be read, "
                "so the declared deliverables were not verified",
            }

    return _read


__all__ = [
    "ARTIFACT_STATUS_READ",
    "DeliverableReader",
    "read_declared_artifacts",
    "workspace_deliverable_reader",
]
