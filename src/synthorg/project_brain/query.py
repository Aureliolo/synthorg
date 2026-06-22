"""Projection and git-history helpers for the project brain.

Keeps :class:`ProjectBrainService` orchestration-only by housing the pure
mappings it delegates to:

* :func:`entry_to_summary` projects a full :class:`BrainEntry` to the lightweight
  :class:`BrainSummary` used by list and board views.
* :func:`entry_to_search_hit` reconstructs a :class:`BrainSearchHit` from an
  indexed memory entry's tags and content.
* :func:`build_git_history` reads the commit log of one entry's JSON snapshot on
  the docs branch and maps it to :class:`BrainEntryVersion` rows. This is the
  git-versioned view of an entry's history; the structured SQL revision chain
  (real authors, full payloads) is served separately by the repository.
"""

import re
from pathlib import Path

from synthorg.core.iso_datetime import parse_git_log_timestamp
from synthorg.core.types import NotBlankStr
from synthorg.engine.workspace._git_subprocess import run_git_subprocess
from synthorg.memory.models import MemoryEntry
from synthorg.observability import get_logger
from synthorg.observability.events.project_brain import BRAIN_HISTORY_READ
from synthorg.persistence.project_brain_protocol import BrainFilterSpec
from synthorg.project_brain.constants import (
    BRAIN_ENTRY_TAG_PREFIX,
    BRAIN_KIND_TAG_PREFIX,
    BRAIN_PROJECT_TAG_PREFIX,
)
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    BrainEntryVersion,
    BrainSearchHit,
    BrainSummary,
)

logger = get_logger(__name__)

_GIT_CMD_TIMEOUT_SECONDS: float = 30.0
_HISTORY_FIELDS_PER_LINE: int = 3
_BRAIN_LOG_FORMAT: str = "%H%x09%aI%x09%s"
_REVISION_RE = re.compile(r"\br(\d+)\b")
# The writer commits brain snapshots under a fixed system identity; the git log
# therefore always attributes the commit to the brain engine, not the logical
# (agent/operator) author. The structured revision chain carries the real one.
_GIT_AUTHOR = NotBlankStr("SynthOrg Project Brain")


def build_filter_spec(  # noqa: PLR0913 -- filter dimensions are explicit
    *,
    project_id: NotBlankStr,
    entry_kind: BrainEntryKind | None = None,
    status: BrainEntryStatus | None = None,
    tag: NotBlankStr | None = None,
    author: NotBlankStr | None = None,
    related_task_id: NotBlankStr | None = None,
) -> BrainFilterSpec:
    """Build a :class:`BrainFilterSpec` from list/count filter dimensions.

    Args:
        project_id: Owning project (always required; the brain is scoped).
        entry_kind: Optional kind filter.
        status: Optional status filter.
        tag: Optional single-tag filter.
        author: Optional author filter.
        related_task_id: Optional filter for entries referencing this task.

    Returns:
        The filter spec for the repository query.
    """
    return BrainFilterSpec(
        project_id=project_id,
        entry_kind=entry_kind,
        status=status,
        tag=tag,
        author=author,
        related_task_id=related_task_id,
    )


def entry_to_summary(entry: BrainEntry) -> BrainSummary:
    """Project a full entry to its list-view summary.

    Args:
        entry: The current-state entry to project.

    Returns:
        The :class:`BrainSummary` for board and list rendering.
    """
    return BrainSummary(
        project_id=entry.project_id,
        entry_id=entry.entry_id,
        revision=entry.revision,
        entry_kind=entry.entry_kind,
        title=NotBlankStr(entry.title),
        status=entry.status,
        author=entry.author,
        recorded_at=entry.recorded_at,
        tags=entry.tags,
    )


def entry_to_search_hit(entry: MemoryEntry) -> BrainSearchHit | None:
    """Reconstruct a search hit from an indexed memory entry.

    Args:
        entry: A memory entry returned from a ``PROJECT_BRAIN`` retrieval.

    Returns:
        The :class:`BrainSearchHit`, or ``None`` when the entry lacks the
        required project / entry / kind tags (and so did not originate here).
    """
    project_id = _extract_tag(entry, BRAIN_PROJECT_TAG_PREFIX)
    entry_id = _extract_tag(entry, BRAIN_ENTRY_TAG_PREFIX)
    entry_kind = _extract_kind(entry)
    if project_id is None or entry_id is None or entry_kind is None:
        return None
    return BrainSearchHit(
        project_id=project_id,
        entry_id=entry_id,
        entry_kind=entry_kind,
        chunk_text=NotBlankStr(entry.content),
        relevance_score=entry.relevance_score or 0.0,
    )


async def build_git_history(
    *,
    repo_root: Path,
    rel_path: str,
    branch: NotBlankStr,
    limit: int,
    offset: int = 0,
) -> tuple[BrainEntryVersion, ...]:
    """Read the snapshot commit log for one entry and map it to versions.

    Args:
        repo_root: The project workspace root.
        rel_path: Path of the entry's JSON snapshot relative to ``repo_root``.
        branch: The docs branch the snapshots commit on.
        limit: Maximum versions to return (newest-first).
        offset: Number of leading commits to skip (``git log --skip``), for
            cursor paging through the full commit log.

    Returns:
        The entry's git versions newest-first; empty when the file has no
        history on the branch (never committed, or the branch is absent).
    """
    rc, stdout, _ = await run_git_subprocess(
        repo_root,
        "log",
        f"--pretty=format:{_BRAIN_LOG_FORMAT}",
        f"-{limit}",
        f"--skip={offset}",
        branch,
        "--",
        rel_path,
        cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
        log_event=BRAIN_HISTORY_READ,
    )
    if rc != 0:
        return ()
    versions = tuple(
        version
        for line in stdout.splitlines()
        if (version := _parse_history_line(line)) is not None
    )
    logger.debug(BRAIN_HISTORY_READ, rel_path=rel_path, count=len(versions))
    return versions


def _parse_history_line(line: str) -> BrainEntryVersion | None:
    r"""Parse one ``git log`` row in ``<sha>\t<author_iso>\t<subject>`` form.

    The revision is recovered from the commit subject the writer stamps
    (``brain(<kind>): <entry_id> r<N>``).

    Returns:
        The parsed :class:`BrainEntryVersion`, or ``None`` when the row has the
        wrong field count, a naive / invalid timestamp, or no revision token.
    """
    parts = line.split("\t", 2)
    if len(parts) != _HISTORY_FIELDS_PER_LINE:
        return None
    sha, committed_at_iso, summary = parts
    committed_at = parse_git_log_timestamp(committed_at_iso)
    if committed_at is None:
        return None
    match = _REVISION_RE.search(summary)
    if match is None:
        return None
    return BrainEntryVersion(
        commit_hash=NotBlankStr(sha),
        revision=int(match.group(1)),
        author=_GIT_AUTHOR,
        committed_at=committed_at,
        summary=NotBlankStr(summary or "(no message)"),
    )


def _extract_tag(entry: MemoryEntry, prefix: str) -> NotBlankStr | None:
    """Return the suffix of the first tag on *entry* carrying *prefix*.

    Returns:
        The tag suffix as a ``NotBlankStr``, or ``None`` when absent / blank.
    """
    for tag in entry.metadata.tags:
        if tag.startswith(prefix):
            suffix = tag[len(prefix) :]
            if suffix.strip():
                return NotBlankStr(suffix)
    return None


def _extract_kind(entry: MemoryEntry) -> BrainEntryKind | None:
    """Pull :class:`BrainEntryKind` out of the entry's ``brain_kind:`` tag.

    Returns:
        The parsed kind, or ``None`` when the tag is missing or not a member.
    """
    raw = _extract_tag(entry, BRAIN_KIND_TAG_PREFIX)
    if raw is None:
        return None
    try:
        return BrainEntryKind(raw)
    except ValueError:
        return None
