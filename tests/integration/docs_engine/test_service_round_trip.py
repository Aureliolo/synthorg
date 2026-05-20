"""End-to-end integration test for :class:`DocsService`.

Wires the real engine (chunker + indexer + writer + service + facade)
against a real git repo via :class:`EmbeddedGitBackend`, a real
:class:`InMemoryBackend`, the in-process fake docs repo, and a real
:class:`ProjectWorkspaceService` rooted at a pytest tmp dir.

Validates the critical write_doc -> commits to the docs
branch, indexes chunks under PROJECT_DOC, and the same doc is later
retrievable via the dashboard read path AND via the search path.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from synthorg.core.enums import DocType, GitBackendType
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_BRANCH_NAME,
    DOCS_WORKSPACE_SUBDIR,
)
from synthorg.docs_engine.errors import DocIndexError, DocNotFoundError
from synthorg.docs_engine.factory import build_docs_service
from synthorg.docs_engine.models import (
    DecisionBlock,
    HeadingBlock,
    ProseBlock,
)
from synthorg.engine.workspace._git_subprocess import run_git_subprocess
from synthorg.engine.workspace.git_backend import (
    GitBackendConfig,
    GitBackendDeps,
    build_git_backend,
)
from synthorg.engine.workspace.project_workspace_service import (
    ProjectWorkspaceService,
)
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.models import MemoryStoreRequest
from tests._shared import FakeClock
from tests.integration.docs_engine._workspace import InMemoryWorkspaceRepo
from tests.unit.api.fakes import FakeDocsRepository

pytestmark = pytest.mark.integration


class _FailingStoreBackend(InMemoryBackend):
    """InMemoryBackend whose ``store`` always raises (drives index failure)."""

    async def store(
        self, agent_id: NotBlankStr, request: MemoryStoreRequest
    ) -> NotBlankStr:
        msg = "forced store failure"
        raise RuntimeError(msg)


async def _build_runtime(
    tmp_path: Path,
    *,
    memory_backend: InMemoryBackend | None = None,
) -> tuple[Any, ProjectWorkspaceService, InMemoryBackend, FakeDocsRepository, Any]:
    config = GitBackendConfig(kind=GitBackendType.EMBEDDED)
    git_backend = build_git_backend(
        config,
        GitBackendDeps(workspace_base_root=tmp_path, clock=FakeClock()),
    )
    workspace_repo = InMemoryWorkspaceRepo()
    workspace_service = ProjectWorkspaceService(
        base_root=tmp_path,
        repo=workspace_repo,
        git_backend=git_backend,
        config=config,
        clock=FakeClock(),
    )
    backend = memory_backend if memory_backend is not None else InMemoryBackend()
    await backend.connect()
    docs_repo = FakeDocsRepository()
    runtime = build_docs_service(
        repo=docs_repo,
        workspace_service=workspace_service,
        git_backend=git_backend,
        memory_backend=backend,
        clock=FakeClock(start=datetime(2026, 5, 20, tzinfo=UTC)),
    )
    return runtime, workspace_service, backend, docs_repo, git_backend


def _sample_body() -> tuple[Any, ...]:
    return (
        HeadingBlock(level=2, text="Summary"),
        ProseBlock(text="Checkout funnel improved by 5% over April."),
        DecisionBlock(
            decision="Hold on funnel rewrite",
            rationale="A/B test still ramping",
        ),
    )


class TestServiceRoundTrip:
    async def test_write_then_read_returns_same_doc(self, tmp_path: Path) -> None:
        runtime, _, backend, _, _ = await _build_runtime(tmp_path)
        try:
            metadata = await runtime.docs_service.write_doc(
                project_id=NotBlankStr("proj-1"),
                title=NotBlankStr("Q2 Status Report"),
                doc_type=DocType.STATUS_REPORT,
                author_agent_id=NotBlankStr("agent_alice"),
                body=_sample_body(),
            )
            assert metadata.slug == "q2-status-report"
            assert metadata.head_commit_sha
            assert metadata.last_indexed_commit_sha == metadata.head_commit_sha

            restored = await runtime.docs_service.read_doc(
                project_id=NotBlankStr("proj-1"),
                slug=metadata.slug,
            )
            assert restored.title == "Q2 Status Report"
            assert len(restored.body) == 3
        finally:
            await backend.disconnect()

    async def test_write_commits_on_docs_branch(self, tmp_path: Path) -> None:
        runtime, ws_svc, backend, _, _ = await _build_runtime(tmp_path)
        try:
            metadata = await runtime.docs_service.write_doc(
                project_id=NotBlankStr("proj-1"),
                title=NotBlankStr("On-disk write"),
                doc_type=DocType.STATUS_REPORT,
                author_agent_id=NotBlankStr("agent_alice"),
                body=(ProseBlock(text="committed"),),
            )
            workspace = await ws_svc.get_or_provision(NotBlankStr("proj-1"))
            repo_root = Path(workspace.workspace_path)
            rc, stdout, _ = await run_git_subprocess(
                repo_root,
                "log",
                "--pretty=format:%H",
                "-1",
                DOCS_BRANCH_NAME,
                cmd_timeout=30.0,
                log_event="test.git_log",
            )
            assert rc == 0
            assert stdout.strip() == metadata.head_commit_sha

            doc_path = (
                repo_root
                / DOCS_WORKSPACE_SUBDIR
                / DocType.STATUS_REPORT.value
                / f"{metadata.slug}.json"
            )
            assert doc_path.exists()
        finally:
            await backend.disconnect()

    async def test_search_returns_indexed_doc(self, tmp_path: Path) -> None:
        runtime, _, backend, _, _ = await _build_runtime(tmp_path)
        try:
            await runtime.docs_service.write_doc(
                project_id=NotBlankStr("proj-1"),
                title=NotBlankStr("Checkout fix"),
                doc_type=DocType.STATUS_REPORT,
                author_agent_id=NotBlankStr("agent_alice"),
                body=(
                    ProseBlock(
                        text="Resolved race condition in checkout submission.",
                    ),
                ),
                tags=(NotBlankStr("checkout"),),
            )
            hits = await runtime.docs_service.search(
                project_id=NotBlankStr("proj-1"),
                query=NotBlankStr("checkout"),
            )
            assert len(hits) >= 1
            assert hits[0].doc_slug == "checkout-fix"
            assert hits[0].doc_type is DocType.STATUS_REPORT
        finally:
            await backend.disconnect()

    async def test_facade_surfaces_doc_for_other_agent(self, tmp_path: Path) -> None:
        """Decision 8a: the facade fans out so a different agent's
        retrieval surfaces project-doc hits."""
        runtime, _, backend, _, _ = await _build_runtime(tmp_path)
        try:
            await runtime.docs_service.write_doc(
                project_id=NotBlankStr("proj-1"),
                title=NotBlankStr("Race condition fix"),
                doc_type=DocType.STATUS_REPORT,
                author_agent_id=NotBlankStr("agent_alice"),
                body=(
                    ProseBlock(
                        text="Race condition in checkout funnel patched.",
                    ),
                ),
            )

            from synthorg.memory.models import MemoryQuery

            entries = await runtime.memory_facade.retrieve(
                agent_id=NotBlankStr("agent_bob"),
                project_id=NotBlankStr("proj-1"),
                query=MemoryQuery(
                    text=NotBlankStr("checkout"),
                    limit=10,
                ),
            )
            assert len(entries) >= 1
            assert any("checkout" in e.content.lower() for e in entries)
        finally:
            await backend.disconnect()

    async def test_reindex_replaces_prior_chunks(self, tmp_path: Path) -> None:
        runtime, _, backend, _, _ = await _build_runtime(tmp_path)
        try:
            first = await runtime.docs_service.write_doc(
                project_id=NotBlankStr("proj-1"),
                title=NotBlankStr("Iter"),
                doc_type=DocType.KNOWLEDGE_NOTE,
                author_agent_id=NotBlankStr("agent_alice"),
                body=(ProseBlock(text="initial content for chunk"),),
            )
            await runtime.docs_service.write_doc(
                project_id=NotBlankStr("proj-1"),
                title=NotBlankStr("Iter"),
                doc_type=DocType.KNOWLEDGE_NOTE,
                author_agent_id=NotBlankStr("agent_alice"),
                body=(ProseBlock(text="updated content for chunk"),),
                slug=first.slug,
            )
            hits = await runtime.docs_service.search(
                project_id=NotBlankStr("proj-1"),
                query=NotBlankStr("content"),
            )
            initial_hits = [h for h in hits if "initial" in h.chunk_text]
            updated_hits = [h for h in hits if "updated" in h.chunk_text]
            assert not initial_hits
            assert updated_hits
        finally:
            await backend.disconnect()

    async def test_write_with_unknown_slug_raises(self, tmp_path: Path) -> None:
        runtime, _, backend, _, _ = await _build_runtime(tmp_path)
        try:
            with pytest.raises(DocNotFoundError):
                await runtime.docs_service.write_doc(
                    project_id=NotBlankStr("proj-1"),
                    title=NotBlankStr("Iter"),
                    doc_type=DocType.KNOWLEDGE_NOTE,
                    author_agent_id=NotBlankStr("agent_alice"),
                    body=(ProseBlock(text="content"),),
                    slug=NotBlankStr("does-not-exist"),
                )
        finally:
            await backend.disconnect()

    async def test_read_missing_raises(self, tmp_path: Path) -> None:
        runtime, _, backend, _, _ = await _build_runtime(tmp_path)
        try:
            with pytest.raises(DocNotFoundError):
                await runtime.docs_service.read_doc(
                    project_id=NotBlankStr("proj-1"),
                    slug=NotBlankStr("missing"),
                )
        finally:
            await backend.disconnect()

    async def test_versioned_read_via_git_show(self, tmp_path: Path) -> None:
        runtime, _, backend, _, _ = await _build_runtime(tmp_path)
        try:
            v1 = await runtime.docs_service.write_doc(
                project_id=NotBlankStr("proj-1"),
                title=NotBlankStr("Versioned"),
                doc_type=DocType.DELIVERABLE,
                author_agent_id=NotBlankStr("agent_alice"),
                body=(ProseBlock(text="version one"),),
            )
            await runtime.docs_service.write_doc(
                project_id=NotBlankStr("proj-1"),
                title=NotBlankStr("Versioned"),
                doc_type=DocType.DELIVERABLE,
                author_agent_id=NotBlankStr("agent_alice"),
                body=(ProseBlock(text="version two"),),
                slug=v1.slug,
            )
            restored_v1 = await runtime.docs_service.read_doc(
                project_id=NotBlankStr("proj-1"),
                slug=v1.slug,
                version=v1.head_commit_sha,
            )
            assert any(
                isinstance(b, ProseBlock) and "version one" in b.text
                for b in restored_v1.body
            )
        finally:
            await backend.disconnect()

    async def test_index_failure_keeps_metadata_behind_head(
        self, tmp_path: Path
    ) -> None:
        """Commit lands but indexing fails: metadata persists with the
        head SHA while ``last_indexed_commit_sha`` stays ``None`` for replay."""
        failing = _FailingStoreBackend()
        runtime, _, backend, docs_repo, _ = await _build_runtime(
            tmp_path, memory_backend=failing
        )
        try:
            with pytest.raises(DocIndexError):
                await runtime.docs_service.write_doc(
                    project_id=NotBlankStr("proj-1"),
                    title=NotBlankStr("Partial"),
                    doc_type=DocType.STATUS_REPORT,
                    author_agent_id=NotBlankStr("agent_alice"),
                    body=(ProseBlock(text="body that fails to index"),),
                )
            meta = await docs_repo.get((NotBlankStr("proj-1"), NotBlankStr("partial")))
            assert meta is not None
            assert meta.head_commit_sha
            assert meta.last_indexed_commit_sha is None
        finally:
            await backend.disconnect()

    async def test_concurrent_same_title_distinct_slugs(self, tmp_path: Path) -> None:
        """Two concurrent same-title creates serialise under the per-project
        write lock and resolve to distinct slugs (no silent overwrite)."""
        runtime, _, backend, docs_repo, _ = await _build_runtime(tmp_path)
        try:

            async def write() -> Any:
                return await runtime.docs_service.write_doc(
                    project_id=NotBlankStr("proj-1"),
                    title=NotBlankStr("Q2 Status"),
                    doc_type=DocType.STATUS_REPORT,
                    author_agent_id=NotBlankStr("agent_alice"),
                    body=(ProseBlock(text="concurrent body"),),
                )

            first, second = await asyncio.gather(write(), write())
            assert first.slug != second.slug
            assert {first.slug, second.slug} == {"q2-status", "q2-status-2"}
            for slug in (first.slug, second.slug):
                assert (
                    await docs_repo.get((NotBlankStr("proj-1"), NotBlankStr(slug)))
                    is not None
                )
        finally:
            await backend.disconnect()
