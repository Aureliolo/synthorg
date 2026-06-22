# module-kind: code
"""Pure transformation helpers for the docs engine service.

Map persistence / memory / git-log records onto the public DTOs
(:class:`DocSummary`, :class:`DocSearchHit`, :class:`DocVersion`). Kept out of
``service`` so the service module stays focused on orchestration rather than
record-shape glue; every function here is side-effect free.
"""

from synthorg.core.iso_datetime import parse_git_log_timestamp
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_PROJECT_TAG_PREFIX,
    DOCS_SLUG_TAG_PREFIX,
    DOCS_TYPE_TAG_PREFIX,
)
from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.models import (
    DocMetadata,
    DocSearchHit,
    DocSummary,
    DocVersion,
)
from synthorg.memory.models import MemoryEntry

_HISTORY_FIELDS_PER_LINE: int = 3


def meta_to_summary(meta: DocMetadata) -> DocSummary:
    """Project a :class:`DocMetadata` row onto its public summary DTO.

    Returns:
        The corresponding ``DocSummary``.
    """
    return DocSummary(
        project_id=meta.project_id,
        slug=meta.slug,
        title=meta.title,
        doc_type=meta.doc_type,
        tags=meta.tags,
        updated_at=meta.updated_at,
    )


def entry_to_hit(
    entry: MemoryEntry,
    *,
    doc_types: frozenset[DocType] | None,
) -> DocSearchHit | None:
    """Convert a memory entry to a search hit; filter by doc_type if given.

    Returns:
        A ``DocSearchHit`` for the entry, or ``None`` when it lacks the
        required tags or its doc type is filtered out.
    """
    project_id = _extract_tag(entry, DOCS_PROJECT_TAG_PREFIX)
    slug = _extract_tag(entry, DOCS_SLUG_TAG_PREFIX)
    if project_id is None or slug is None:
        return None
    doc_type = _extract_doc_type(entry)
    if doc_type is None:
        return None
    if doc_types is not None and doc_type not in doc_types:
        return None
    return DocSearchHit(
        project_id=project_id,
        doc_slug=slug,
        doc_type=doc_type,
        chunk_text=entry.content,
        relevance_score=entry.relevance_score or 0.0,
    )


def _extract_tag(entry: MemoryEntry, prefix: str) -> NotBlankStr | None:
    for tag in entry.metadata.tags:
        if tag.startswith(prefix):
            suffix = tag[len(prefix) :]
            if suffix.strip():
                return NotBlankStr(suffix)
    return None


def _extract_doc_type(entry: MemoryEntry) -> DocType | None:
    """Pull DocType out of the entry's ``doc_type:<value>`` tag.

    Returns:
        The parsed ``DocType``, or ``None`` when the tag is missing or
        not a valid member.
    """
    raw = _extract_tag(entry, DOCS_TYPE_TAG_PREFIX)
    if raw is None:
        return None
    try:
        return DocType(raw)
    except ValueError:
        return None


def parse_history_line(line: str) -> DocVersion | None:
    r"""Parse one ``git log`` row in ``<sha>\\t<author_iso>\\t<subject>`` form.

    The second field is the git author date (``%aI``); it is stored on the
    ``DocVersion.committed_at`` field (the docs engine commits as a single
    system author, so author and committer dates coincide).

    Returns:
        The parsed ``DocVersion``, or ``None`` when the row has the wrong
        field count or a naive / invalid timestamp.
    """
    parts = line.split("\t", 2)
    if len(parts) != _HISTORY_FIELDS_PER_LINE:
        return None
    sha, committed_at_iso, summary = parts
    committed_at = parse_git_log_timestamp(committed_at_iso)
    if committed_at is None:
        return None
    return DocVersion(
        commit_sha=NotBlankStr(sha),
        author_agent_id=NotBlankStr("docs_engine"),
        committed_at=committed_at,
        summary=NotBlankStr(summary or "(no message)"),
    )
