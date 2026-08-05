# module-kind: code
"""Read the files a task declared, so a reviewer judges the deliverable.

The completion oracle's peer reviewer is the most load-bearing gate in the
chain: fail-closed, on by default, reviewing every task. Until now the
"deliverable" it read was the agent's own closing message, so an APPROVE
verdict said the agent wrote a convincing summary, not that the work is
there. This reads what the task promised: the files at its declared paths.

Content is bounded twice, per file and in total, because a reviewer prompt
is a fixed budget and one large generated file would otherwise crowd out
every other deliverable. Truncation is announced in the text rather than
silent, so the reviewer knows it is judging an excerpt.

The files are agent-written and therefore untrusted: whatever the reviewer
receives is fenced by the caller with ``wrap_untrusted`` before it reaches
a prompt.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Final

from synthorg.core.artifact import ExpectedArtifact
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.red_team import RED_TEAM_NO_DELIVERABLE
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

#: Marker closing a file whose content was cut at the per-file bound.
_TRUNCATED_NOTE: Final[str] = "\n... (truncated)"

#: Marker replacing the files dropped once the total bound was reached.
_OMITTED_NOTE: Final[str] = "... ({count} further artifact(s) omitted)"

#: Reported in place of content for a declared path that is not a file.
_ABSENT_NOTE: Final[str] = "(not produced)"
_DIRECTORY_NOTE: Final[str] = "(directory)"
_UNREADABLE_NOTE: Final[str] = "(unreadable: {reason})"

#: Resolves ``(project_id, expected) -> the deliverable text``, or ``None``
#: when nothing readable was produced. Async because the bounds are operator
#: settings read live per review, so a retune arms the next review rather
#: than the next boot.
type DeliverableReader = Callable[
    [str, Sequence[ExpectedArtifact]], Awaitable[str | None]
]


def read_declared_artifacts(
    expected: Sequence[ExpectedArtifact],
    *,
    workspace: Path,
    max_bytes_per_file: int,
    max_total_bytes: int,
) -> str | None:
    """Assemble the declared artifacts' content into one reviewable text.

    Args:
        expected: The artifacts the task declared it would produce.
        workspace: The project's workspace directory.
        max_bytes_per_file: Per-file content bound, in characters.
        max_total_bytes: Total content bound across every file.

    Returns:
        A path-labelled rendering of what was produced, or ``None`` when
        the task declared nothing.
    """
    if not expected:
        return None
    root = workspace.resolve()
    sections: list[str] = []
    budget = max_total_bytes
    for index, artifact in enumerate(expected):
        if budget <= 0:
            sections.append(_OMITTED_NOTE.format(count=len(expected) - index))
            break
        body = _read_one(artifact, root=root, limit=min(max_bytes_per_file, budget))
        budget -= len(body)
        sections.append(f"--- {artifact.path} ---\n{body}")
    return "\n\n".join(sections)


def _read_one(artifact: ExpectedArtifact, *, root: Path, limit: int) -> str:
    """Read one declared artifact, bounded at *limit* characters.

    Returns:
        The file's (possibly truncated) text, or a note naming why there
        is none. A note is content too: "not produced" is exactly what a
        reviewer needs to see, and hiding it would leave the reviewer
        judging a deliverable it does not know is missing.
    """
    candidate = Path(artifact.path)
    resolved = (
        candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    )
    if not candidate.is_absolute() and not resolved.is_relative_to(root):
        # A path the run could not legitimately have written is not the
        # task's output, so it is reported as absent rather than read.
        return _ABSENT_NOTE
    if not resolved.exists():
        return _ABSENT_NOTE
    if resolved.is_dir():
        return _DIRECTORY_NOTE
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _UNREADABLE_NOTE.format(reason=safe_error_description(exc))
    if len(text) <= limit:
        return text
    return text[:limit] + _TRUNCATED_NOTE


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
    ) -> str | None:
        """Read *project_id*'s declared artifacts.

        Returns:
            The deliverable text, or ``None`` when it could not be read.
        """
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
            # A reviewer given no artifacts falls back to the closing
            # message; refusing to review at all would be worse.
            logger.warning(
                RED_TEAM_NO_DELIVERABLE,
                reason="artifact_read_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None

    return _read


__all__ = [
    "DeliverableReader",
    "read_declared_artifacts",
    "workspace_deliverable_reader",
]
