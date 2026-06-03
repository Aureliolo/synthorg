"""End-to-end integration test for the long-horizon project brain.

Wires the real engine (chunker + indexer + writer + service) against a real git
repo via the embedded git backend, a real :class:`InMemoryBackend`, the
in-process fake brain repo, and a real :class:`ProjectWorkspaceService` rooted at
a pytest tmp dir, plus a real :class:`ProjectAwareMemoryFacade` with the brain
leg enabled.

The centrepiece (:meth:`test_resume_answers_decided_open_blocked`) verifies that
after entries are written by one agent, a *different* agent on a later task can
answer "what is decided / open / blocked, and why" purely through the transparent
retrieval facade -- not by re-deriving -- with brain content fenced under
``<brain-state>``.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest

from synthorg.core.enums import GitBackendType
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.retrieval_facade import ProjectAwareMemoryFacade
from synthorg.engine.prompt_safety import TAG_BRAIN_STATE
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
from synthorg.memory.models import MemoryQuery, MemoryStoreRequest
from synthorg.project_brain.constants import (
    BRAIN_BRANCH_NAME,
    BRAIN_WORKSPACE_SUBDIR,
)
from synthorg.project_brain.factory import build_project_brain_service
from synthorg.project_brain.models import (
    BlockerPayload,
    BlockerSeverity,
    BrainEntryKind,
    BrainEntryStatus,
    DecisionPayload,
    OpenQuestionPayload,
)
from tests._shared import FakeClock
from tests.integration.docs_engine._workspace import InMemoryWorkspaceRepo
from tests.unit.api.fakes import FakeProjectBrainRepository

pytestmark = pytest.mark.integration

_PROJECT = NotBlankStr("proj-1")
_ALICE = NotBlankStr("agent_alice")
_BOB = NotBlankStr("agent_bob")
_GIT_TIMEOUT = 30.0


class _FailingStoreBackend(InMemoryBackend):
    """InMemoryBackend whose ``store`` always raises (drives index failure)."""

    @override
    async def store(
        self, agent_id: NotBlankStr, request: MemoryStoreRequest
    ) -> NotBlankStr:
        msg = "forced store failure"
        raise RuntimeError(msg)


class _BrainHarness:
    """Bundle of wired collaborators for a brain integration scenario."""

    __slots__ = ("backend", "facade", "repo", "runtime", "workspace_service")

    def __init__(
        self,
        *,
        runtime: object,
        facade: ProjectAwareMemoryFacade,
        workspace_service: ProjectWorkspaceService,
        backend: InMemoryBackend,
        repo: FakeProjectBrainRepository,
    ) -> None:
        self.runtime = runtime
        self.facade = facade
        self.workspace_service = workspace_service
        self.backend = backend
        self.repo = repo


async def _build(
    tmp_path: Path,
    *,
    memory_backend: InMemoryBackend | None = None,
    repo: FakeProjectBrainRepository | None = None,
) -> _BrainHarness:
    config = GitBackendConfig(kind=GitBackendType.EMBEDDED)
    git_backend = build_git_backend(
        config,
        GitBackendDeps(workspace_base_root=tmp_path, clock=FakeClock()),
    )
    workspace_service = ProjectWorkspaceService(
        base_root=tmp_path,
        repo=InMemoryWorkspaceRepo(),
        git_backend=git_backend,
        config=config,
        clock=FakeClock(),
    )
    backend = memory_backend if memory_backend is not None else InMemoryBackend()
    await backend.connect()
    # A caller can pass a populated repo to model a restart against the same
    # durable store (boot-replay scenarios).
    repo = repo if repo is not None else FakeProjectBrainRepository()
    runtime = build_project_brain_service(
        repo=repo,
        workspace_service=workspace_service,
        git_backend=git_backend,
        memory_backend=backend,
        clock=FakeClock(start=datetime(2026, 5, 30, tzinfo=UTC)),
    )
    facade = ProjectAwareMemoryFacade(backend=backend, brain_enabled=True)
    return _BrainHarness(
        runtime=runtime,
        facade=facade,
        workspace_service=workspace_service,
        backend=backend,
        repo=repo,
    )


async def _seed_resume_state(harness: _BrainHarness) -> None:
    """One agent records a decision, an open question, and a blocker."""
    service = harness.runtime.brain_service  # type: ignore[attr-defined]
    await service.append_entry(
        project_id=_PROJECT,
        title=NotBlankStr("Adopt event-sourced checkout"),
        rationale=NotBlankStr("We need a full audit trail of checkout state."),
        status=BrainEntryStatus.ACCEPTED,
        author=_ALICE,
        payload=DecisionPayload(decision_outcome=NotBlankStr("event-sourcing")),
    )
    await service.append_entry(
        project_id=_PROJECT,
        title=NotBlankStr("Which checkout queue backend?"),
        rationale=NotBlankStr("Throughput target for checkout is still unclear."),
        status=BrainEntryStatus.OPEN,
        author=_ALICE,
        payload=OpenQuestionPayload(),
    )
    await service.append_entry(
        project_id=_PROJECT,
        title=NotBlankStr("Checkout staging environment is down"),
        rationale=NotBlankStr("The checkout staging runner is offline."),
        status=BrainEntryStatus.BLOCKED,
        author=_ALICE,
        payload=BlockerPayload(severity=BlockerSeverity.HIGH),
    )


class TestProjectBrainRoundTrip:
    async def test_append_then_read_returns_same_entry(self, tmp_path: Path) -> None:
        harness = await _build(tmp_path)
        try:
            service = harness.runtime.brain_service  # type: ignore[attr-defined]
            entry = await service.append_entry(
                project_id=_PROJECT,
                title=NotBlankStr("Adopt append-only storage"),
                rationale=NotBlankStr("History matters."),
                status=BrainEntryStatus.ACCEPTED,
                author=_ALICE,
                payload=DecisionPayload(decision_outcome=NotBlankStr("append-only")),
            )
            assert entry.revision == 1
            current = await service.get_current(
                project_id=_PROJECT, entry_id=entry.entry_id
            )
            assert current is not None
            assert current.title == "Adopt append-only storage"
        finally:
            await harness.backend.disconnect()

    async def test_append_commits_on_docs_branch(self, tmp_path: Path) -> None:
        harness = await _build(tmp_path)
        try:
            service = harness.runtime.brain_service  # type: ignore[attr-defined]
            entry = await service.append_entry(
                project_id=_PROJECT,
                title=NotBlankStr("On-disk write"),
                rationale=NotBlankStr("committed"),
                status=BrainEntryStatus.ACCEPTED,
                author=_ALICE,
                payload=DecisionPayload(decision_outcome=NotBlankStr("x")),
            )
            workspace = await harness.workspace_service.get_or_provision(_PROJECT)
            repo_root = Path(workspace.workspace_path)
            rc, stdout, _ = await run_git_subprocess(
                repo_root,
                "log",
                "--pretty=format:%s",
                "-1",
                BRAIN_BRANCH_NAME,
                cmd_timeout=_GIT_TIMEOUT,
                log_event="test.git_log",
            )
            assert rc == 0
            assert f"r{entry.revision}" in stdout
            entry_path = (
                repo_root
                / BRAIN_WORKSPACE_SUBDIR
                / entry.entry_kind.value
                / f"{entry.entry_id}.json"
            )
            assert entry_path.exists()

            versions = await service.git_history(
                project_id=_PROJECT, entry_id=entry.entry_id
            )
            assert len(versions) == 1
            assert versions[0].revision == 1
        finally:
            await harness.backend.disconnect()

    async def test_revision_updates_current_state(self, tmp_path: Path) -> None:
        harness = await _build(tmp_path)
        try:
            service = harness.runtime.brain_service  # type: ignore[attr-defined]
            entry = await service.append_entry(
                project_id=_PROJECT,
                title=NotBlankStr("Which queue?"),
                rationale=NotBlankStr("unclear"),
                status=BrainEntryStatus.OPEN,
                author=_ALICE,
                payload=OpenQuestionPayload(),
            )
            resolved = await service.resolve(
                project_id=_PROJECT,
                entry_id=entry.entry_id,
                author=_BOB,
                answer=NotBlankStr("Use the durable queue."),
            )
            assert resolved.revision == 2
            current = await service.get_current(
                project_id=_PROJECT, entry_id=entry.entry_id
            )
            assert current is not None
            assert current.status is BrainEntryStatus.RESOLVED
        finally:
            await harness.backend.disconnect()

    async def test_search_returns_indexed_entry(self, tmp_path: Path) -> None:
        harness = await _build(tmp_path)
        try:
            service = harness.runtime.brain_service  # type: ignore[attr-defined]
            await service.append_entry(
                project_id=_PROJECT,
                title=NotBlankStr("Checkout race condition decision"),
                rationale=NotBlankStr("Resolved race in checkout submission."),
                status=BrainEntryStatus.ACCEPTED,
                author=_ALICE,
                payload=DecisionPayload(decision_outcome=NotBlankStr("serialise")),
            )
            hits = await service.query(
                project_id=_PROJECT, query=NotBlankStr("checkout")
            )
            assert len(hits) == 1
            assert hits[0].entry_kind is BrainEntryKind.DECISION
        finally:
            await harness.backend.disconnect()

    async def test_resume_answers_decided_open_blocked(self, tmp_path: Path) -> None:
        """A different agent resumes and answers what is decided / open /
        blocked through transparent retrieval."""
        harness = await _build(tmp_path)
        try:
            await _seed_resume_state(harness)

            # A different agent, later, retrieves project memory transparently.
            entries = await harness.facade.retrieve(
                agent_id=_BOB,
                project_id=_PROJECT,
                query=MemoryQuery(text=NotBlankStr("checkout"), limit=20),
            )
            brain_hits = [e for e in entries if f"<{TAG_BRAIN_STATE}>" in e.content]
            assert brain_hits, "brain state must surface on transparent re-entry"
            corpus = "\n".join(e.content for e in brain_hits).lower()
            # The resuming agent sees each entry's distinctive content -- the
            # decision outcome, the open question's subject, and the blocker's
            # subject -- not merely the kind tags the chunker always emits.
            assert "decision" in corpus
            assert "open_question" in corpus
            assert "blocker" in corpus
            assert "event-sourcing" in corpus  # the decision outcome
            assert "queue backend" in corpus  # the open question subject
            assert "staging" in corpus  # the blocker subject
            assert f"</{TAG_BRAIN_STATE}>" in "\n".join(e.content for e in brain_hits)
        finally:
            await harness.backend.disconnect()

    async def test_structured_current_state_lists_each_kind(
        self, tmp_path: Path
    ) -> None:
        """The operator path: list_current answers the same question structurally."""
        harness = await _build(tmp_path)
        try:
            await _seed_resume_state(harness)
            service = harness.runtime.brain_service  # type: ignore[attr-defined]
            blocked = await service.list_current(
                project_id=_PROJECT, status=BrainEntryStatus.BLOCKED
            )
            assert len(blocked) == 1
            assert blocked[0].entry_kind is BrainEntryKind.BLOCKER
            decisions = await service.list_current(
                project_id=_PROJECT, entry_kind=BrainEntryKind.DECISION
            )
            assert len(decisions) == 1
            assert decisions[0].status is BrainEntryStatus.ACCEPTED
        finally:
            await harness.backend.disconnect()

    async def test_multi_session_gap_resume_answers_from_brain(
        self, tmp_path: Path
    ) -> None:
        """A reconstructed service answers decided/open/blocked over durable stores.

        Models a multi-session resume: the durable stores (a persistent
        memory backend -- here a reused InMemoryBackend instance -- plus the
        SQL brain repo and the git workspace) survive while the service and
        facade objects are rebuilt, as on a process restart. The structured
        ``list_current`` reads go through the SQL repo, so they prove a fresh
        service reads persisted state, not a shared in-process cache.
        Volatile-index recovery (boot replay of a persisted-but-unindexed
        entry) is covered separately by ``test_boot_replay_heals_unindexed_gap``.
        """
        first = await _build(tmp_path)
        try:
            await _seed_resume_state(first)

            resumed = await _build(
                tmp_path,
                memory_backend=first.backend,
                repo=first.repo,
            )
            entries = await resumed.facade.retrieve(
                agent_id=_BOB,
                project_id=_PROJECT,
                query=MemoryQuery(text=NotBlankStr("checkout"), limit=20),
            )
            brain_hits = [e for e in entries if f"<{TAG_BRAIN_STATE}>" in e.content]
            assert brain_hits, "brain state must survive a multi-session gap"
            corpus = "\n".join(e.content for e in brain_hits).lower()
            assert "event-sourcing" in corpus  # the decision outcome
            assert "queue backend" in corpus  # the open question subject
            assert "staging" in corpus  # the blocker subject

            # The structured durable path answers the same question through
            # the freshly-built service, proving it reads persisted SQL state.
            service = resumed.runtime.brain_service  # type: ignore[attr-defined]
            decided = await service.list_current(
                project_id=_PROJECT, status=BrainEntryStatus.ACCEPTED
            )
            open_qs = await service.list_current(
                project_id=_PROJECT, status=BrainEntryStatus.OPEN
            )
            blocked = await service.list_current(
                project_id=_PROJECT, status=BrainEntryStatus.BLOCKED
            )
            assert len(decided) == 1
            assert len(open_qs) == 1
            assert len(blocked) == 1
            assert {d.entry_kind for d in decided} == {BrainEntryKind.DECISION}
            assert {q.entry_kind for q in open_qs} == {BrainEntryKind.OPEN_QUESTION}
            assert {b.entry_kind for b in blocked} == {BrainEntryKind.BLOCKER}
        finally:
            await first.backend.disconnect()

    async def test_reindex_replaces_prior_chunks(self, tmp_path: Path) -> None:
        harness = await _build(tmp_path)
        try:
            service = harness.runtime.brain_service  # type: ignore[attr-defined]
            entry = await service.append_entry(
                project_id=_PROJECT,
                title=NotBlankStr("Original wording"),
                rationale=NotBlankStr("first pass"),
                status=BrainEntryStatus.OPEN,
                author=_ALICE,
                payload=OpenQuestionPayload(),
            )
            await service.revise_entry(
                project_id=_PROJECT,
                entry_id=entry.entry_id,
                author=_ALICE,
                title=NotBlankStr("Revised wording"),
            )
            hits = await service.query(
                project_id=_PROJECT, query=NotBlankStr("wording"), limit=20
            )
            # Only the current revision's chunks remain (no duplicate entry ids).
            entry_ids = {h.entry_id for h in hits}
            assert entry_ids == {entry.entry_id}
        finally:
            await harness.backend.disconnect()

    async def test_boot_replay_heals_unindexed_gap(self, tmp_path: Path) -> None:
        """An entry persisted while indexing was down is re-indexed at boot."""
        # Indexing fails, so the entry lands in SQL but not the index.
        failing = _FailingStoreBackend()
        harness = await _build(tmp_path, memory_backend=failing)
        try:
            service = harness.runtime.brain_service  # type: ignore[attr-defined]
            entry = await service.append_entry(
                project_id=_PROJECT,
                title=NotBlankStr("Payments risk accepted"),
                rationale=NotBlankStr("We accept the payments chargeback risk."),
                status=BrainEntryStatus.ACCEPTED,
                author=_ALICE,
                payload=DecisionPayload(decision_outcome=NotBlankStr("accept")),
            )
            # Durable in SQL, but never indexed (no index-state row).
            assert await harness.repo.indexed_revisions(_PROJECT) == {}
        finally:
            await failing.disconnect()

        # A healthy boot replays the gap. Reuse the populated repo so the boot
        # sees the persisted-but-unindexed entry.
        healthy = InMemoryBackend()
        harness2 = await _build(tmp_path, memory_backend=healthy, repo=harness.repo)
        try:
            reindexed = await harness2.runtime.replay_unindexed(  # type: ignore[attr-defined]
                project_ids=(_PROJECT,)
            )
            assert reindexed == 1
            assert await harness2.repo.indexed_revisions(_PROJECT) == {
                entry.entry_id: 1
            }
            entries = await harness2.facade.retrieve(
                agent_id=_BOB,
                project_id=_PROJECT,
                query=MemoryQuery(text=NotBlankStr("payments"), limit=10),
            )
            assert any(f"<{TAG_BRAIN_STATE}>" in e.content for e in entries)
        finally:
            await healthy.disconnect()
