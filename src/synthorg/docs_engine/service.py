# module-kind: service
"""Top-level service for the living-documentation engine.

Composes the slug helper, chunker, indexer, writer, and the docs
metadata repository into a single async entry point. Agents call
:class:`DocsService` indirectly via :class:`WriteLivingDocTool` /
:class:`SearchLivingDocsTool` and via the MCP handlers; the REST
endpoints call it directly for read-only operations.
"""

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.concurrency import RefcountedLockMap
from synthorg.core.iso_datetime import parse_git_log_timestamp
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.chunker import DocChunker
from synthorg.docs_engine.constants import (
    DOCS_BRANCH_NAME,
    DOCS_HISTORY_DEFAULT_LIMIT,
    DOCS_LIST_DEFAULT_LIMIT,
    DOCS_MEMORY_NAMESPACE,
    DOCS_PROJECT_TAG_PREFIX,
    DOCS_SEARCH_DEFAULT_LIMIT,
    DOCS_SEARCH_MAX_LIMIT,
    DOCS_SLUG_TAG_PREFIX,
    DOCS_TYPE_TAG_PREFIX,
    DOCS_WORKSPACE_SUBDIR,
    SYSTEM_DOCS_AGENT_ID,
)
from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.errors import (
    DocCommitError,
    DocIndexError,
    DocNotFoundError,
    DocValidationError,
)
from synthorg.docs_engine.indexer import DocIndexer
from synthorg.docs_engine.models import (
    DocBlock,
    DocMetadata,
    DocSearchHit,
    DocSummary,
    DocVersion,
    LivingDocument,
)
from synthorg.docs_engine.serializer import deserialize_doc
from synthorg.docs_engine.slug import derive_slug
from synthorg.docs_engine.writer import DocWriter
from synthorg.engine.workspace._git_subprocess import run_git_subprocess
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docs import (
    DOC_HISTORY_READ,
    DOC_NOT_FOUND,
    DOC_RETRIEVED,
    DOC_SEARCH_COMPLETE,
    DOC_SEARCH_START,
    DOC_SLUG_DERIVED,
)
from synthorg.persistence.docs_protocol import DocsFilterSpec, DocsRepository

if TYPE_CHECKING:
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )

logger = get_logger(__name__)

_GIT_CMD_TIMEOUT_SECONDS: float = 30.0
_HISTORY_FIELDS_PER_LINE: int = 3
_MIN_SHA_LENGTH: int = 7
_MAX_SHA_LENGTH: int = 40
_COMMIT_SHA_RE = re.compile(rf"^[0-9a-fA-F]{{{_MIN_SHA_LENGTH},{_MAX_SHA_LENGTH}}}$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_EXISTING_SLUGS_PAGE_SIZE: int = 1_024


def _validate_slug(slug: NotBlankStr) -> None:
    """Reject slugs that are not safe kebab-case identifiers.

    Slugs flow into filesystem paths (``<type>/<slug>.json``) and git
    pathspecs, so an unvalidated value such as ``../../etc/passwd`` would
    be a path-traversal sink.

    Raises:
        DocValidationError: When ``slug`` is not a safe kebab-case
            identifier.
    """
    if _SLUG_RE.match(slug) is None:
        msg = f"invalid slug format: {slug!r}"
        raise DocValidationError(msg)


class DocsService:
    """Public entry point for living-doc operations."""

    __slots__ = (
        "_backend",
        "_chunker",
        "_clock",
        "_indexer",
        "_repo",
        "_workspace_service",
        "_write_locks",
        "_writer",
    )

    def __init__(  # noqa: PLR0913 -- engine entry point composes every collaborator
        self,
        *,
        repo: DocsRepository,
        workspace_service: ProjectWorkspaceService,
        chunker: DocChunker,
        indexer: DocIndexer,
        writer: DocWriter,
        backend: MemoryBackend,
        clock: Clock | None = None,
    ) -> None:
        self._repo = repo
        self._workspace_service = workspace_service
        self._chunker = chunker
        self._indexer = indexer
        self._writer = writer
        self._backend = backend
        self._clock: Clock = clock if clock is not None else SystemClock()
        # Serialises slug derivation with the write per project so two
        # concurrent same-title writes cannot derive the same slug and
        # silently overwrite one another; evicts the lock once idle.
        self._write_locks: RefcountedLockMap[NotBlankStr] = RefcountedLockMap()

    async def write_doc(  # noqa: PLR0913 -- doc fields are intentionally explicit
        self,
        *,
        project_id: NotBlankStr,
        title: NotBlankStr,
        doc_type: DocType,
        author_agent_id: NotBlankStr,
        body: tuple[DocBlock, ...],
        tags: tuple[NotBlankStr, ...] = (),
        related_task_ids: tuple[NotBlankStr, ...] = (),
        slug: NotBlankStr | None = None,
    ) -> DocMetadata:
        """Create or update a living document.

        When *slug* is ``None`` (the normal case for agent tool calls),
        the service derives a fresh slug from *title* against the
        existing project + doc_type bucket. Supplying an explicit
        *slug* updates an existing doc in place (the update path).

        Args:
            project_id: Owning project.
            title: Human-readable title; drives slug derivation.
            doc_type: Taxonomy bucket.
            author_agent_id: Authoring agent's id.
            body: Tuple of typed :data:`DocBlock` instances.
            tags: Free-form classification tags.
            related_task_ids: Associated task IDs.
            slug: Pre-existing slug for the update path; ``None`` for
                create.

        Returns:
            The fresh :class:`DocMetadata` row.

        Raises:
            DocCommitError: Workspace write / commit / push failed.
            DocIndexError: Chunk indexing into memory failed (the
                commit lands on disk; ``last_indexed_commit_sha`` stays
                behind for replay).
        """
        async with self._write_locks.acquire(project_id):
            resolved_slug, prior = await self._resolve_slug(
                project_id=project_id,
                title=title,
                doc_type=doc_type,
                supplied_slug=slug,
            )
            now = self._clock.now()
            created_at = prior.created_at if prior is not None else now
            doc = LivingDocument(
                slug=resolved_slug,
                title=title,
                doc_type=doc_type,
                tags=tags,
                related_task_ids=related_task_ids,
                author_agent_id=author_agent_id,
                body=body,
                created_at=created_at,
                updated_at=now,
            )
            write_result = await self._writer.write(project_id=project_id, doc=doc)
            chunks = self._chunker.chunk(project_id=project_id, doc=doc)
            last_indexed: NotBlankStr | None
            try:
                await self._indexer.index(
                    project_id=project_id, slug=resolved_slug, chunks=chunks
                )
                last_indexed = write_result.commit_sha
            except DocIndexError:
                last_indexed = (
                    prior.last_indexed_commit_sha if prior is not None else None
                )
                metadata = DocMetadata(
                    project_id=project_id,
                    slug=resolved_slug,
                    doc_type=doc_type,
                    title=title,
                    tags=tags,
                    related_task_ids=related_task_ids,
                    head_commit_sha=write_result.commit_sha,
                    last_indexed_commit_sha=last_indexed,
                    created_at=created_at,
                    updated_at=now,
                )
                await self._repo.save(metadata)
                raise
            metadata = DocMetadata(
                project_id=project_id,
                slug=resolved_slug,
                doc_type=doc_type,
                title=title,
                tags=tags,
                related_task_ids=related_task_ids,
                head_commit_sha=write_result.commit_sha,
                last_indexed_commit_sha=last_indexed,
                created_at=created_at,
                updated_at=now,
            )
            await self._repo.save(metadata)
            return metadata

    async def read_doc(
        self,
        *,
        project_id: NotBlankStr,
        slug: NotBlankStr,
        version: NotBlankStr | None = None,
    ) -> LivingDocument:
        """Read a living document by slug, optionally at a historical SHA.

        Args:
            project_id: Owning project.
            slug: Doc slug.
            version: When ``None``, reads current bytes from the
                workspace tip. When set, must be a SHA that exists on
                :data:`DOCS_BRANCH_NAME`; the doc is read via
                ``git show <sha>:<path>``.

        Returns:
            The deserialised document.

        Raises:
            DocValidationError: ``version`` is not a valid commit SHA.
            DocNotFoundError: Slug or version not found.
        """
        _validate_slug(slug)
        if version is not None and _COMMIT_SHA_RE.match(version) is None:
            msg = (
                f"version {version!r} is not a valid commit SHA "
                f"(expected {_MIN_SHA_LENGTH}-{_MAX_SHA_LENGTH} hex chars)"
            )
            raise DocValidationError(msg)
        metadata = await self._repo.get((project_id, slug))
        if metadata is None:
            logger.info(DOC_NOT_FOUND, project_id=project_id, slug=slug)
            msg = f"living doc {project_id!r}/{slug!r} not found"
            raise DocNotFoundError(msg)
        workspace = await self._workspace_service.get_or_provision(project_id)
        repo_root = Path(workspace.workspace_path)
        rel_path = f"{DOCS_WORKSPACE_SUBDIR}/{metadata.doc_type.value}/{slug}.json"
        raw_bytes = await self._read_bytes(
            repo_root=repo_root,
            rel_path=rel_path,
            version=version,
        )
        doc = deserialize_doc(raw_bytes)
        logger.debug(
            DOC_RETRIEVED,
            project_id=project_id,
            slug=slug,
            version=version,
        )
        return doc

    async def list_docs(
        self,
        *,
        project_id: NotBlankStr,
        doc_type: DocType | None = None,
        tag: NotBlankStr | None = None,
        limit: int = DOCS_LIST_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[DocSummary, ...]:
        """List docs for a project (newest first).

        Returns:
            The matching document summaries, newest-first, for the
            requested filter window.
        """
        spec = DocsFilterSpec(
            project_id=project_id,
            doc_type=doc_type,
            tag=tag,
        )
        rows = await self._repo.query(spec, limit=limit, offset=offset)
        return tuple(_meta_to_summary(row) for row in rows)

    async def search(
        self,
        *,
        project_id: NotBlankStr,
        query: NotBlankStr,
        doc_types: frozenset[DocType] | None = None,
        limit: int = DOCS_SEARCH_DEFAULT_LIMIT,
    ) -> tuple[DocSearchHit, ...]:
        """Semantic search over indexed chunks for a project.

        Args:
            project_id: Owning project.
            query: Search text.
            doc_types: Optional filter on doc taxonomy bucket.
            limit: Maximum hits to return (bounded by
                :data:`DOCS_SEARCH_MAX_LIMIT`).

        Returns:
            Tuple of :class:`DocSearchHit` ordered by descending
            relevance.
        """
        effective_limit = min(limit, DOCS_SEARCH_MAX_LIMIT)
        logger.debug(
            DOC_SEARCH_START,
            project_id=project_id,
            limit=effective_limit,
            doc_types=tuple(t.value for t in doc_types) if doc_types else None,
        )
        project_tag = NotBlankStr(f"{DOCS_PROJECT_TAG_PREFIX}{project_id}")
        entries = await self._backend.retrieve(
            SYSTEM_DOCS_AGENT_ID,
            MemoryQuery(
                text=query,
                categories=frozenset({MemoryCategory.PROJECT_DOC}),
                namespaces=frozenset({DOCS_MEMORY_NAMESPACE}),
                tags=(project_tag,),
                limit=effective_limit,
            ),
        )
        hits = tuple(
            hit
            for entry in entries
            if (hit := _entry_to_hit(entry, doc_types=doc_types)) is not None
            and hit.project_id == project_id
        )
        logger.info(
            DOC_SEARCH_COMPLETE,
            project_id=project_id,
            hit_count=len(hits),
        )
        return hits

    async def history(
        self,
        *,
        project_id: NotBlankStr,
        slug: NotBlankStr,
        limit: int = DOCS_HISTORY_DEFAULT_LIMIT,
    ) -> tuple[DocVersion, ...]:
        """Return commit history for one doc.

        Args:
            project_id: Owning project.
            slug: Doc slug.
            limit: Maximum history entries.

        Returns:
            Tuple of :class:`DocVersion` ordered newest-first.

        Raises:
            DocNotFoundError: Slug not found in the metadata repo.
        """
        _validate_slug(slug)
        metadata = await self._repo.get((project_id, slug))
        if metadata is None:
            msg = f"living doc {project_id!r}/{slug!r} not found"
            raise DocNotFoundError(msg)
        workspace = await self._workspace_service.get_or_provision(project_id)
        repo_root = Path(workspace.workspace_path)
        rel_path = f"{DOCS_WORKSPACE_SUBDIR}/{metadata.doc_type.value}/{slug}.json"
        rc, stdout, stderr = await run_git_subprocess(
            repo_root,
            "log",
            "--pretty=format:%H%x09%aI%x09%s",
            f"-{limit}",
            DOCS_BRANCH_NAME,
            "--",
            rel_path,
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=DOC_HISTORY_READ,
        )
        if rc != 0:
            msg = (
                f"git log failed for {project_id!r}/{slug!r}: "
                f"{stderr.strip() or 'unknown error'}"
            )
            raise DocNotFoundError(msg)
        versions = tuple(
            v
            for line in stdout.splitlines()
            if (v := _parse_history_line(line)) is not None
        )
        logger.debug(
            DOC_HISTORY_READ,
            project_id=project_id,
            slug=slug,
            count=len(versions),
        )
        return versions

    async def _resolve_slug(
        self,
        *,
        project_id: NotBlankStr,
        title: NotBlankStr,
        doc_type: DocType,
        supplied_slug: NotBlankStr | None,
    ) -> tuple[NotBlankStr, DocMetadata | None]:
        """Return (slug, prior_metadata) for create / update branch.

        Raises:
            DocNotFoundError: When an explicit ``supplied_slug`` targets a
                document that does not exist (the update path).
        """
        if supplied_slug is not None:
            _validate_slug(supplied_slug)
            prior = await self._repo.get((project_id, supplied_slug))
            if prior is None:
                msg = (
                    f"living doc {project_id!r}/{supplied_slug!r} not found; "
                    f"an explicit slug targets the update path"
                )
                raise DocNotFoundError(msg)
            return supplied_slug, prior
        spec = DocsFilterSpec(project_id=project_id, doc_type=doc_type)
        existing_slugs: set[NotBlankStr] = set()
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded pagination drain
        while True:
            page = await self._repo.query(
                spec, limit=_EXISTING_SLUGS_PAGE_SIZE, offset=offset
            )
            if not page:
                break
            existing_slugs.update(row.slug for row in page)
            if len(page) < _EXISTING_SLUGS_PAGE_SIZE:
                break
            offset += _EXISTING_SLUGS_PAGE_SIZE
        slug = derive_slug(title, existing_slugs=existing_slugs)
        logger.debug(
            DOC_SLUG_DERIVED,
            project_id=project_id,
            title=title,
            slug=slug,
        )
        return slug, None

    async def _read_bytes(
        self,
        *,
        repo_root: Path,
        rel_path: str,
        version: NotBlankStr | None,
    ) -> bytes:
        """Read JSON bytes from disk (current) or via git show (historical).

        Returns:
            The raw document JSON bytes for the requested version (current
            working tree when ``version`` is ``None``).

        Raises:
            DocNotFoundError: When the file or the requested version is
                absent / unreachable.
            DocCommitError: When a current-tree read fails with an OS
                error.
        """
        if version is None:
            full_path = repo_root / rel_path
            try:
                return await asyncio.to_thread(full_path.read_bytes)
            except FileNotFoundError as exc:
                msg = f"living doc bytes not found at {rel_path!r}"
                raise DocNotFoundError(msg) from exc
            except OSError as exc:
                msg = (
                    f"failed to read living doc bytes at {rel_path!r}: "
                    f"{safe_error_description(exc)}"
                )
                raise DocCommitError(msg) from exc
        ancestry_rc, _, _ = await run_git_subprocess(
            repo_root,
            "merge-base",
            "--is-ancestor",
            version,
            DOCS_BRANCH_NAME,
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=DOC_NOT_FOUND,
        )
        if ancestry_rc != 0:
            msg = f"version {version!r} is not reachable from {DOCS_BRANCH_NAME!r}"
            raise DocNotFoundError(msg)
        rc, stdout_text, stderr = await run_git_subprocess(
            repo_root,
            "show",
            f"{version}:{rel_path}",
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=DOC_NOT_FOUND,
        )
        if rc != 0:
            msg = (
                f"git show {version}:{rel_path} failed: "
                f"{stderr.strip() or 'unknown error'}"
            )
            raise DocNotFoundError(msg)
        return stdout_text.encode("utf-8")


def _meta_to_summary(meta: DocMetadata) -> DocSummary:
    return DocSummary(
        project_id=meta.project_id,
        slug=meta.slug,
        title=meta.title,
        doc_type=meta.doc_type,
        tags=meta.tags,
        updated_at=meta.updated_at,
    )


def _entry_to_hit(
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


def _parse_history_line(line: str) -> DocVersion | None:
    r"""Parse one ``git log`` row in ``<sha>\\t<author_iso>\\t<subject>`` form.

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
