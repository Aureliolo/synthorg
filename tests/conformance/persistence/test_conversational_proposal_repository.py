"""Conformance tests for ``ConversationalProposalRepository``.

Not exposed on ``PersistenceBackend`` (the lifecycle wires it
directly), so the repo is built over the migrated ``backend.get_db()``
handle. SQLite + Postgres share one assertion set.
"""

from datetime import UTC, datetime
from typing import cast

import aiosqlite
import pytest

from synthorg.core.enums import ConversationalProposalStatus
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import ConversationalProposal
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalFilterSpec,
    ConversationalProposalRepository,
)
from synthorg.persistence.postgres.conversational_proposal_repo import (
    PostgresConversationalProposalRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.conversational_proposal_repo import (
    SQLiteConversationalProposalRepository,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _repo(
    backend: PersistenceBackend,
) -> ConversationalProposalRepository:
    """Return a concrete proposal repository bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteConversationalProposalRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresConversationalProposalRepository(
            cast("AsyncConnectionPool", handle)
        )
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _make_proposal(
    *,
    proposal_id: str = "prop-001",
    conversation_id: str = "conv-001",
    approval_id: str = "appr-001",
    status: ConversationalProposalStatus = (ConversationalProposalStatus.PENDING),
) -> ConversationalProposal:
    return ConversationalProposal(
        id=proposal_id,
        conversation_id=conversation_id,
        approval_id=approval_id,
        work_item_json='{"title": "Build landing page"}',
        status=status,
        created_at=_NOW,
    )


class TestConversationalProposalRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        proposal = _make_proposal()
        await repo.save(proposal)

        fetched = await repo.get(proposal.id)
        assert fetched is not None
        assert fetched.id == proposal.id
        assert fetched.approval_id == "appr-001"
        assert fetched.status is ConversationalProposalStatus.PENDING
        assert fetched.work_item_json == '{"title": "Build landing page"}'
        assert fetched.created_at.tzinfo is not None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get("prop-missing") is None

    async def test_save_commits_visible_to_fresh_repo(
        self, backend: PersistenceBackend
    ) -> None:
        first = _repo(backend)
        proposal = _make_proposal(proposal_id="prop-commit")
        await first.save(proposal)

        second = _repo(backend)
        fetched = await second.get(proposal.id)
        assert fetched is not None

    async def test_query_filter_by_approval_id(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_proposal(proposal_id="p1", approval_id="appr-A"))
        await repo.save(_make_proposal(proposal_id="p2", approval_id="appr-B"))

        rows = await repo.query(
            ConversationalProposalFilterSpec(approval_id=NotBlankStr("appr-A"))
        )
        assert {r.id for r in rows} == {"p1"}

    async def test_query_filter_by_conversation_id(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(
            _make_proposal(
                proposal_id="pc1",
                conversation_id="conv-X",
                approval_id="a-pc1",
            )
        )
        await repo.save(
            _make_proposal(
                proposal_id="pc2",
                conversation_id="conv-Y",
                approval_id="a-pc2",
            )
        )

        rows = await repo.query(
            ConversationalProposalFilterSpec(conversation_id=NotBlankStr("conv-X"))
        )
        assert {r.id for r in rows} == {"pc1"}

    async def test_query_filter_by_status(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(
            _make_proposal(
                proposal_id="ps-pending",
                approval_id="a-ps1",
                status=ConversationalProposalStatus.PENDING,
            )
        )
        await repo.save(
            _make_proposal(
                proposal_id="ps-executed",
                approval_id="a-ps2",
                status=ConversationalProposalStatus.EXECUTED,
            )
        )

        rows = await repo.query(
            ConversationalProposalFilterSpec(
                status=ConversationalProposalStatus.EXECUTED
            )
        )
        ids = {r.id for r in rows}
        assert "ps-executed" in ids
        assert "ps-pending" not in ids

    async def test_count(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_proposal(proposal_id="c1", approval_id="a-c1"))
        await repo.save(_make_proposal(proposal_id="c2", approval_id="a-c2"))
        assert await repo.count(ConversationalProposalFilterSpec()) >= 2

    async def test_transition_if_flips_state_atomically(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        proposal = _make_proposal(proposal_id="t-prop", approval_id="a-t")
        await repo.save(proposal)

        result = await repo.transition_if(
            proposal.id,
            from_state=ConversationalProposalStatus.PENDING,
            to_state=ConversationalProposalStatus.EXECUTED,
        )
        assert result is True
        fetched = await repo.get(proposal.id)
        assert fetched is not None
        assert fetched.status is ConversationalProposalStatus.EXECUTED

    async def test_transition_if_returns_false_on_mismatch(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        proposal = _make_proposal(
            proposal_id="t-mismatch",
            approval_id="a-tm",
            status=ConversationalProposalStatus.REJECTED,
        )
        await repo.save(proposal)

        result = await repo.transition_if(
            proposal.id,
            from_state=ConversationalProposalStatus.PENDING,
            to_state=ConversationalProposalStatus.EXECUTED,
        )
        assert result is False

    async def test_delete_returns_true_then_false(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        proposal = _make_proposal(proposal_id="d-prop", approval_id="a-d")
        await repo.save(proposal)
        assert await repo.delete(proposal.id) is True
        assert await repo.get(proposal.id) is None
        assert await repo.delete(proposal.id) is False

    async def test_protocol_runtime_check(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        assert isinstance(repo, ConversationalProposalRepository)
