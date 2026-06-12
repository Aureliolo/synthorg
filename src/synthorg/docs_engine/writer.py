"""Writes serialised :class:`LivingDocument` bytes to the project workspace.

A doc write follows this sequence:

1. Resolve the persistent workspace via :class:`ProjectWorkspaceService`.
2. Ensure the dedicated docs branch (:data:`DOCS_BRANCH_NAME`) exists
   locally, branching from the project's default branch on first use.
3. Write the JSON payload at ``<workspace>/<DOCS_WORKSPACE_SUBDIR>/<type>/<slug>.json``.
4. Stage + commit on the docs branch with a stable subject line.
5. Push via the configured :class:`GitBackend`.

The writer is the only docs-engine module that talks to git directly;
its operations are bounded by per-project locking inside
:class:`ProjectWorkspaceService` (lazy provision) plus per-project
serialisation in the writer itself (concurrent writes on the same
project sequence through one ``asyncio.Lock``).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_BRANCH_NAME,
    DOCS_WORKSPACE_SUBDIR,
)
from synthorg.docs_engine.errors import DocCommitError
from synthorg.docs_engine.models import LivingDocument
from synthorg.docs_engine.serializer import serialize_doc
from synthorg.engine.workspace._git_subprocess import run_git_subprocess
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.docs import (
    DOC_COMMIT_PUSHED,
    DOC_WRITE_FAILED,
    DOC_WRITTEN,
)

if TYPE_CHECKING:
    from synthorg.engine.workspace.git_backend import GitBackend
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )

logger = get_logger(__name__)

_GIT_CMD_TIMEOUT_SECONDS: float = 30.0


class DocWriteResult:
    """In-memory tuple bundling commit metadata for the service.

    Not a Pydantic model because callers only consume it transiently;
    promoting it would force a frozen invariant for negligible benefit.
    """

    __slots__ = ("commit_sha", "doc_path")

    def __init__(self, *, commit_sha: NotBlankStr, doc_path: Path) -> None:
        self.commit_sha = commit_sha
        self.doc_path = doc_path


class DocWriter:
    """Serialises docs to disk and commits them on the docs branch."""

    __slots__ = (
        "_git_backend",
        "_locks",
        "_locks_guard",
        "_locks_refcounts",
        "_workspace_service",
    )

    def __init__(
        self,
        *,
        workspace_service: ProjectWorkspaceService,
        git_backend: GitBackend,
    ) -> None:
        self._workspace_service = workspace_service
        self._git_backend = git_backend
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._locks_refcounts: dict[str, int] = {}

    @asynccontextmanager
    async def _lock_for(self, project_id: str) -> AsyncIterator[None]:
        """Serialise writes per project, evicting the lock when idle.

        Refcounted so a process that writes docs for many projects over its
        lifetime does not retain one ``asyncio.Lock`` per project forever.

        Yields:
            Control while the per-project lock is held.
        """
        async with self._locks_guard:
            lock = self._locks.get(project_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[project_id] = lock
            self._locks_refcounts[project_id] = (
                self._locks_refcounts.get(project_id, 0) + 1
            )
        try:
            async with lock:
                yield
        finally:
            async with self._locks_guard:
                remaining = self._locks_refcounts[project_id] - 1
                if remaining == 0:
                    del self._locks_refcounts[project_id]
                    del self._locks[project_id]
                else:
                    self._locks_refcounts[project_id] = remaining

    async def write(
        self,
        *,
        project_id: NotBlankStr,
        doc: LivingDocument,
    ) -> DocWriteResult:
        """Serialise, commit, and push *doc* under *project_id*.

        Args:
            project_id: Owning project.
            doc: Document to persist (slug + body must be valid).

        Returns:
            :class:`DocWriteResult` carrying the commit SHA and the
            on-disk path.

        Raises:
            DocCommitError: Any phase (workspace resolution, file
                write, git add/commit, or push) failed.
        """
        async with self._lock_for(project_id):
            return await self._write_locked(project_id=project_id, doc=doc)

    async def _write_locked(
        self,
        *,
        project_id: NotBlankStr,
        doc: LivingDocument,
    ) -> DocWriteResult:
        try:
            workspace = await self._workspace_service.get_or_provision(project_id)
            repo_root = Path(workspace.workspace_path)
            await self._ensure_docs_branch(
                repo_root=repo_root,
                default_branch=workspace.default_branch,
            )
            doc_path = self._doc_path(repo_root=repo_root, doc=doc)
            await asyncio.to_thread(doc_path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(doc_path.write_bytes, serialize_doc(doc))
            commit_sha = await self._stage_commit(
                repo_root=repo_root, doc_path=doc_path, doc=doc
            )
            await self._git_backend.push(
                project_id=project_id,
                repo_root=repo_root,
                branch=DOCS_BRANCH_NAME,
                base_branch=workspace.default_branch,
            )
        except DocCommitError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            msg = (
                f"Failed to write living doc {project_id!r}/{doc.slug!r} "
                f"on {DOCS_BRANCH_NAME!r}"
            )
            logger.warning(
                DOC_WRITE_FAILED,
                project_id=project_id,
                slug=doc.slug,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DocCommitError(msg) from exc
        logger.info(
            DOC_WRITTEN,
            project_id=project_id,
            slug=doc.slug,
            doc_type=doc.doc_type.value,
            commit_sha=commit_sha,
        )
        logger.info(
            DOC_COMMIT_PUSHED,
            project_id=project_id,
            slug=doc.slug,
            branch=DOCS_BRANCH_NAME,
            commit_sha=commit_sha,
        )
        return DocWriteResult(commit_sha=commit_sha, doc_path=doc_path)

    @staticmethod
    def _doc_path(*, repo_root: Path, doc: LivingDocument) -> Path:
        """Return the canonical on-disk path for *doc*."""
        return (
            repo_root / DOCS_WORKSPACE_SUBDIR / doc.doc_type.value / f"{doc.slug}.json"
        )

    async def _ensure_docs_branch(
        self,
        *,
        repo_root: Path,
        default_branch: NotBlankStr,
    ) -> None:
        """Switch to (or create) the docs branch in *repo_root*.

        Raises:
            DocCommitError: When the checkout or branch-create git command
                fails.
        """
        rc, stdout, _ = await run_git_subprocess(
            repo_root,
            "rev-parse",
            "--verify",
            f"refs/heads/{DOCS_BRANCH_NAME}",
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=DOC_WRITE_FAILED,
        )
        if rc == 0 and stdout.strip():
            rc_checkout, _, stderr = await run_git_subprocess(
                repo_root,
                "checkout",
                DOCS_BRANCH_NAME,
                cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
                log_event=DOC_WRITE_FAILED,
            )
            if rc_checkout != 0:
                msg = (
                    f"git checkout {DOCS_BRANCH_NAME} failed: "
                    f"{stderr.strip() or 'unknown error'}"
                )
                raise DocCommitError(msg)
            return
        rc_branch, _, stderr = await run_git_subprocess(
            repo_root,
            "checkout",
            "-b",
            DOCS_BRANCH_NAME,
            default_branch,
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=DOC_WRITE_FAILED,
        )
        if rc_branch != 0:
            msg = (
                f"git checkout -b {DOCS_BRANCH_NAME} from "
                f"{default_branch} failed: {stderr.strip() or 'unknown error'}"
            )
            raise DocCommitError(msg)

    async def _stage_commit(
        self,
        *,
        repo_root: Path,
        doc_path: Path,
        doc: LivingDocument,
    ) -> NotBlankStr:
        """Stage and commit *doc_path*; return the new HEAD SHA.

        Returns:
            The new HEAD commit SHA after staging and committing the doc.

        Raises:
            DocCommitError: When the ``git add`` or ``git commit`` step
                fails.
        """
        rel = doc_path.relative_to(repo_root).as_posix()
        rc_add, _, stderr_add = await run_git_subprocess(
            repo_root,
            "add",
            "--",
            rel,
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=DOC_WRITE_FAILED,
        )
        if rc_add != 0:
            msg = f"git add {rel} failed: {stderr_add.strip() or 'unknown error'}"
            raise DocCommitError(msg)
        message = f"docs({doc.doc_type.value}): update {doc.slug}"
        rc_commit, _, stderr_commit = await run_git_subprocess(
            repo_root,
            "-c",
            "user.email=docs@synthorg.local",
            "-c",
            "user.name=SynthOrg Docs Engine",
            "commit",
            "--allow-empty",
            "-m",
            message,
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=DOC_WRITE_FAILED,
        )
        if rc_commit != 0:
            msg = (
                f"git commit failed for {rel}: "
                f"{stderr_commit.strip() or 'unknown error'}"
            )
            raise DocCommitError(msg)
        rc_sha, sha_out, stderr_sha = await run_git_subprocess(
            repo_root,
            "rev-parse",
            "HEAD",
            cmd_timeout=_GIT_CMD_TIMEOUT_SECONDS,
            log_event=DOC_WRITE_FAILED,
        )
        if rc_sha != 0 or not sha_out.strip():
            msg = f"git rev-parse HEAD failed: {stderr_sha.strip() or 'no output'}"
            raise DocCommitError(msg)
        return NotBlankStr(sha_out.strip())
