"""Brownfield codebase intake orchestration service.

Imports an existing codebase into a project's persistent workspace, scans
it into a navigable structure map, and indexes it into the hybrid-retrieval
knowledge store. The whole sequence is serialised per project so two
concurrent imports cannot interleave at seed / scan / persist / ingest.

The persisted structure-map row is the "already imported" marker: a
same-source re-import re-scans in place (idempotent), while a *different*
source onto an occupied project is refused.
"""

import asyncio
import re
from pathlib import Path
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.codebase_structure_map import CodebaseStructureMap
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr
from synthorg.engine.brownfield.errors import BrownfieldWorkspaceNotEmptyError
from synthorg.engine.brownfield.models import (
    CodebaseImportResult,
    CodebaseImportSubmission,
)
from synthorg.engine.brownfield.scanner import scan_codebase
from synthorg.engine.brownfield.scanner.protocol import StructureMapScanner
from synthorg.engine.brownfield.source_resolver import BrownfieldSourceResolver
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)
from synthorg.knowledge.enums import SourceType
from synthorg.knowledge.service import KnowledgeService
from synthorg.observability import get_logger
from synthorg.observability.events.brownfield import (
    BROWNFIELD_CODEBASE_INDEXED,
    BROWNFIELD_IMPORT_COMPLETED,
    BROWNFIELD_IMPORT_REJECTED,
    BROWNFIELD_IMPORT_STARTED,
    BROWNFIELD_STRUCTURE_SCANNED,
    BROWNFIELD_STRUCTURE_UNCHANGED,
    BROWNFIELD_WORKSPACE_SEEDED,
)
from synthorg.persistence.codebase_structure_map_protocol import (
    CodebaseStructureMapRepository,
)

logger = get_logger(__name__)

_URL_USERINFO: Final[re.Pattern[str]] = re.compile(
    # RFC 3986 scheme: ALPHA *( ALPHA / DIGIT / "+" / "-" / "." ). The
    # earlier ``\w+`` form missed compound schemes like ``git+ssh://``.
    r"([A-Za-z][A-Za-z0-9+\-.]*://)[^/@\s]+@"
)


def _redact_source_ref(source_ref: str) -> str:
    """Strip ``user:token@`` userinfo from a source URL before logging.

    The resolver rejects credential-bearing remote refs, but the import-started
    log fires before resolution, so redact defensively here too.

    Returns:
        The source ref with any ``user:token@`` userinfo replaced by
        ``[REDACTED]@`` ; unchanged when no userinfo is present.
    """
    return _URL_USERINFO.sub(r"\1[REDACTED]@", source_ref)


class BrownfieldImportService:
    """Orchestrates importing, mapping, and indexing an existing codebase."""

    def __init__(  # noqa: PLR0913 -- collaborators are the orchestration boundary
        self,
        *,
        workspace_service: ProjectWorkspaceService,
        source_resolver: BrownfieldSourceResolver,
        scanners: tuple[StructureMapScanner, ...],
        structure_map_repo: CodebaseStructureMapRepository,
        knowledge_service: KnowledgeService,
        clock: Clock | None = None,
    ) -> None:
        self._workspaces = workspace_service
        self._resolver = source_resolver
        self._scanners = scanners
        self._repo = structure_map_repo
        self._knowledge = knowledge_service
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, project_id: str) -> asyncio.Lock:
        """Return the per-project import lock, creating it on first use.

        Args:
            project_id: Project whose imports must be serialised.

        Returns:
            The :class:`asyncio.Lock` guarding imports for ``project_id``;
            the same instance is returned on every subsequent call.
        """
        async with self._locks_guard:
            return self._locks.setdefault(project_id, asyncio.Lock())

    async def import_codebase(
        self, submission: CodebaseImportSubmission
    ) -> CodebaseImportResult:
        """Import the codebase at ``submission.source_ref`` into its project.

        Returns:
            A :class:`CodebaseImportResult` describing the imported
            structure map; a re-import outcome when a structure map
            already exists for this project, a fresh-import outcome
            otherwise.

        Raises:
            BrownfieldWorkspaceNotEmptyError: A different codebase is
                already imported into this project.
            BrownfieldSourceUnavailableError: The source cannot be read.
            GitBackendSeedError: The seed (clone/copy) failed.
        """
        project_id = submission.project_id
        lock = await self._lock_for(project_id)
        async with lock:
            logger.info(
                BROWNFIELD_IMPORT_STARTED,
                project_id=project_id,
                source_ref=_redact_source_ref(submission.source_ref),
            )
            workspace = await self._workspaces.get_or_provision(project_id)
            repo_root = Path(workspace.workspace_path)
            existing = await self._repo.get(project_id)
            if existing is not None:
                return await self._reimport(submission, repo_root, existing)
            return await self._fresh_import(submission, workspace, repo_root)

    async def _fresh_import(
        self,
        submission: CodebaseImportSubmission,
        workspace: ProjectWorkspace,
        repo_root: Path,
    ) -> CodebaseImportResult:
        """Seed, scan, and index a codebase into an empty project.

        Indexes before persisting the structure map so a failed index
        leaves no "already imported" marker behind.

        Args:
            submission: The import request being fulfilled.
            workspace: The provisioned workspace for the project.
            repo_root: Filesystem root the source is seeded into.

        Returns:
            A fresh-import :class:`CodebaseImportResult`.
        """
        resolved = await self._resolver.resolve(submission.source_ref)
        await self._workspaces.git_backend.seed(
            project_id=submission.project_id,
            repo_root=repo_root,
            source=resolved,
            default_branch=workspace.default_branch,
        )
        logger.info(
            BROWNFIELD_WORKSPACE_SEEDED,
            project_id=submission.project_id,
            source_kind=resolved.source_kind.value,
        )
        structure_map = await self._scan(submission, repo_root)
        # Index BEFORE persisting the structure map: if ``_index`` raises,
        # the save never happens, so the next attempt re-enters
        # ``_fresh_import`` instead of taking the ``_reimport`` early-return
        # path with an unindexed codebase persisted.
        knowledge_source_id = await self._index(submission, repo_root)
        await self._repo.save(structure_map)
        logger.info(
            BROWNFIELD_IMPORT_COMPLETED,
            project_id=submission.project_id,
            module_count=len(structure_map.modules),
        )
        return self._result(structure_map, knowledge_source_id, unchanged=False)

    async def _reimport(
        self,
        submission: CodebaseImportSubmission,
        repo_root: Path,
        existing: CodebaseStructureMap,
    ) -> CodebaseImportResult:
        """Re-scan an already-imported project in place.

        A matching source re-scans idempotently and short-circuits to an
        unchanged result when the content hash is identical; a different
        source onto an occupied project is refused.

        Args:
            submission: The import request being fulfilled.
            repo_root: Filesystem root of the existing checkout.
            existing: The persisted structure map for this project.

        Returns:
            A re-import :class:`CodebaseImportResult` (unchanged when the
            re-scan matches the persisted content hash).

        Raises:
            BrownfieldWorkspaceNotEmptyError: ``submission`` names a
                different source than the one already imported.
        """
        if existing.source_ref != submission.source_ref:
            logger.warning(
                BROWNFIELD_IMPORT_REJECTED,
                project_id=submission.project_id,
                reason="different_source",
            )
            raise BrownfieldWorkspaceNotEmptyError(project_id=submission.project_id)
        rescanned = await self._scan(submission, repo_root)
        if rescanned.content_hash == existing.content_hash:
            logger.info(
                BROWNFIELD_STRUCTURE_UNCHANGED,
                project_id=submission.project_id,
                content_hash=existing.content_hash,
            )
            return self._result(existing, None, unchanged=True)
        # Index before save here too, for the same reason as the fresh path:
        # a failed re-index must not leave a stale rescanned map persisted.
        knowledge_source_id = await self._index(submission, repo_root)
        await self._repo.save(rescanned)
        return self._result(rescanned, knowledge_source_id, unchanged=False)

    async def _scan(
        self, submission: CodebaseImportSubmission, repo_root: Path
    ) -> CodebaseStructureMap:
        """Scan the checked-out source into a structure map.

        Args:
            submission: The import request being fulfilled.
            repo_root: Filesystem root to scan.

        Returns:
            The :class:`CodebaseStructureMap` describing modules and
            dependencies discovered under ``repo_root``.
        """
        structure_map = await scan_codebase(
            workspace_path=repo_root,
            project_id=submission.project_id,
            source_ref=submission.source_ref,
            scanners=self._scanners,
            clock=self._clock,
        )
        logger.info(
            BROWNFIELD_STRUCTURE_SCANNED,
            project_id=submission.project_id,
            module_count=len(structure_map.modules),
            dependency_count=len(structure_map.dependencies),
        )
        return structure_map

    async def _index(
        self, submission: CodebaseImportSubmission, repo_root: Path
    ) -> NotBlankStr:
        """Ingest the checked-out source into the knowledge store.

        Args:
            submission: The import request being fulfilled.
            repo_root: Filesystem root to ingest.

        Returns:
            The knowledge-source id assigned to the ingested codebase.
        """
        source = await self._knowledge.ingest(
            source_type=SourceType.REPO,
            uri=NotBlankStr(str(repo_root)),
            title=submission.title,
            project_id=submission.project_id,
        )
        logger.info(
            BROWNFIELD_CODEBASE_INDEXED,
            project_id=submission.project_id,
            knowledge_source_id=source.source_id,
        )
        return NotBlankStr(source.source_id)

    @staticmethod
    def _result(
        structure_map: CodebaseStructureMap,
        knowledge_source_id: NotBlankStr | None,
        *,
        unchanged: bool,
    ) -> CodebaseImportResult:
        """Build the import result DTO from a structure map.

        Args:
            structure_map: The scanned (or existing) structure map.
            knowledge_source_id: Knowledge-source id, or ``None`` when the
                re-scan was unchanged and no re-index occurred.
            unchanged: Whether the re-scan matched the persisted map.

        Returns:
            The assembled :class:`CodebaseImportResult`.
        """
        return CodebaseImportResult(
            project_id=structure_map.project_id,
            source_ref=structure_map.source_ref,
            content_hash=NotBlankStr(structure_map.content_hash),
            module_count=len(structure_map.modules),
            dependency_count=len(structure_map.dependencies),
            knowledge_source_id=knowledge_source_id,
            unchanged=unchanged,
        )


__all__ = ["BrownfieldImportService"]
