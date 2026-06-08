"""Conformance tests for ``ConversationInviteRepository`` (SQLite + Postgres).

Not exposed on ``PersistenceBackend`` (the conversational factory wires
it directly), so the repo is built over the migrated
``backend.get_db()`` handle. SQLite + Postgres share one assertion set,
catching divergence in the nullable ``target_role`` column and the
``status`` CHECK.

The source-CHECK test pins the cross-backend behaviour the invite
feature depends on: the Postgres ``approvals.source`` CHECK admits
``'conversational_invite'`` while SQLite leaves ``source`` as free TEXT,
so a consent approval can be durably persisted on both backends.
"""

from datetime import UTC, datetime
from typing import cast

import aiosqlite
import pytest

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ConversationStatus
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.enums import (
    ConversationInviteStatus,
    ConversationKind,
)
from synthorg.meta.chief_of_staff.group_models import ConversationInvite
from synthorg.meta.chief_of_staff.models import Conversation
from synthorg.persistence.approval_protocol import ApprovalRepository
from synthorg.persistence.conversation_invite_protocol import (
    ConversationInviteFilterSpec,
    ConversationInviteRepository,
)
from synthorg.persistence.conversation_protocol import ConversationRepository
from synthorg.persistence.postgres.approval_repo import PostgresApprovalRepository
from synthorg.persistence.postgres.conversation_invite_repo import (
    PostgresConversationInviteRepository,
)
from synthorg.persistence.postgres.conversation_repo import (
    PostgresConversationRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.approval_repo import SQLiteApprovalRepository
from synthorg.persistence.sqlite.conversation_invite_repo import (
    SQLiteConversationInviteRepository,
)
from synthorg.persistence.sqlite.conversation_repo import (
    SQLiteConversationRepository,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _repo(backend: PersistenceBackend) -> ConversationInviteRepository:
    """Return a concrete invite repository bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteConversationInviteRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresConversationInviteRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _conversation_repo(backend: PersistenceBackend) -> ConversationRepository:
    """Return a concrete ``ConversationRepository`` bound to *backend*.

    The invite table has a ``conversation_id`` FK to ``conversations``;
    every test seeds its parent conversation row first or ``save()``
    raises a foreign-key violation.
    """
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteConversationRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresConversationRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _approval_repo(backend: PersistenceBackend) -> ApprovalRepository:
    """Return a concrete ``ApprovalRepository`` bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteApprovalRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresApprovalRepository(cast("AsyncConnectionPool", handle))
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


async def _seed_conversation(
    backend: PersistenceBackend, *, conversation_id: str = "conv-001"
) -> None:
    """Save the parent conversation row required by the invite FK."""
    await _conversation_repo(backend).save(
        Conversation(
            id=NotBlankStr(conversation_id),
            created_by=NotBlankStr("user-001"),
            created_at=_NOW,
            updated_at=_NOW,
            status=ConversationStatus.ACTIVE,
            kind=ConversationKind.GROUP,
        )
    )


def _make_invite(  # noqa: PLR0913 -- test builder: many independent knobs
    *,
    invite_id: str = "inv-001",
    conversation_id: str = "conv-001",
    approval_id: str = "appr-001",
    target_agent_id: str = "agent-cfo",
    target_role: str | None = "CFO",
    status: ConversationInviteStatus = ConversationInviteStatus.PENDING,
) -> ConversationInvite:
    return ConversationInvite(
        id=NotBlankStr(invite_id),
        conversation_id=NotBlankStr(conversation_id),
        approval_id=NotBlankStr(approval_id),
        requested_by_agent_id=NotBlankStr("agent-ceo"),
        target_agent_id=NotBlankStr(target_agent_id),
        target_role=NotBlankStr(target_role) if target_role is not None else None,
        reason=NotBlankStr("budget sign-off needed"),
        status=status,
        created_at=_NOW,
    )


class TestConversationInviteRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await _seed_conversation(backend)
        repo = _repo(backend)
        invite = _make_invite()
        await repo.save(invite)

        fetched = await repo.get(invite.id)
        assert fetched is not None
        assert fetched.id == invite.id
        assert fetched.approval_id == "appr-001"
        assert fetched.requested_by_agent_id == "agent-ceo"
        assert fetched.target_agent_id == "agent-cfo"
        assert fetched.target_role == "CFO"
        assert fetched.reason == "budget sign-off needed"
        assert fetched.status is ConversationInviteStatus.PENDING
        assert fetched.created_at.tzinfo is not None

    async def test_null_target_role_round_trips(
        self, backend: PersistenceBackend
    ) -> None:
        # An invite by agent name (not role) carries no ``target_role``;
        # the nullable column must survive the round trip as ``None``.
        await _seed_conversation(backend)
        repo = _repo(backend)
        invite = _make_invite(invite_id="inv-norole", target_role=None)
        await repo.save(invite)

        fetched = await repo.get(invite.id)
        assert fetched is not None
        assert fetched.target_role is None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get("inv-missing") is None

    async def test_save_commits_visible_to_fresh_repo(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_conversation(backend)
        first = _repo(backend)
        invite = _make_invite(invite_id="inv-commit")
        await first.save(invite)

        second = _repo(backend)
        assert await second.get(invite.id) is not None

    async def test_query_filter_by_approval_id(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_conversation(backend)
        repo = _repo(backend)
        # Distinct targets: the partial-unique index admits only one
        # PENDING invite per (conversation, target_agent_id).
        await repo.save(_make_invite(invite_id="i1", approval_id="appr-A"))
        await repo.save(
            _make_invite(
                invite_id="i2", approval_id="appr-B", target_agent_id="agent-coo"
            )
        )

        rows = await repo.query(
            ConversationInviteFilterSpec(approval_id=NotBlankStr("appr-A"))
        )
        assert {r.id for r in rows} == {"i1"}

    async def test_query_filter_by_conversation_id(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_conversation(backend, conversation_id="conv-X")
        await _seed_conversation(backend, conversation_id="conv-Y")
        repo = _repo(backend)
        await repo.save(
            _make_invite(invite_id="ic1", conversation_id="conv-X", approval_id="a-ic1")
        )
        await repo.save(
            _make_invite(invite_id="ic2", conversation_id="conv-Y", approval_id="a-ic2")
        )

        rows = await repo.query(
            ConversationInviteFilterSpec(conversation_id=NotBlankStr("conv-X"))
        )
        assert {r.id for r in rows} == {"ic1"}

    async def test_query_filter_by_target_and_status(
        self, backend: PersistenceBackend
    ) -> None:
        # The park path's duplicate check filters by
        # (conversation_id, target_agent_id, status=PENDING).
        await _seed_conversation(backend)
        repo = _repo(backend)
        await repo.save(
            _make_invite(
                invite_id="it-pending",
                approval_id="a-itp",
                target_agent_id="agent-cfo",
                status=ConversationInviteStatus.PENDING,
            )
        )
        await repo.save(
            _make_invite(
                invite_id="it-declined",
                approval_id="a-itd",
                target_agent_id="agent-cfo",
                status=ConversationInviteStatus.DECLINED,
            )
        )

        rows = await repo.query(
            ConversationInviteFilterSpec(
                conversation_id=NotBlankStr("conv-001"),
                target_agent_id=NotBlankStr("agent-cfo"),
                status=ConversationInviteStatus.PENDING,
            )
        )
        assert {r.id for r in rows} == {"it-pending"}

    async def test_count(self, backend: PersistenceBackend) -> None:
        await _seed_conversation(backend)
        repo = _repo(backend)
        # Distinct targets: the partial-unique index admits only one
        # PENDING invite per (conversation, target_agent_id).
        await repo.save(_make_invite(invite_id="ic-a", approval_id="a-ca"))
        await repo.save(
            _make_invite(
                invite_id="ic-b", approval_id="a-cb", target_agent_id="agent-coo"
            )
        )
        assert await repo.count(ConversationInviteFilterSpec()) >= 2

    async def test_duplicate_pending_invite_rejected(
        self, backend: PersistenceBackend
    ) -> None:
        # The partial-unique index ``WHERE status = 'pending'`` admits
        # only one PENDING invite per (conversation_id, target_agent_id);
        # a second distinct invite for the same target must be rejected
        # at the DB layer (the hard backstop behind the app-layer dedup).
        await _seed_conversation(backend)
        repo = _repo(backend)
        await repo.save(
            _make_invite(
                invite_id="dup-1", approval_id="a-dup-1", target_agent_id="agent-cfo"
            )
        )
        with pytest.raises(ConstraintViolationError):
            await repo.save(
                _make_invite(
                    invite_id="dup-2",
                    approval_id="a-dup-2",
                    target_agent_id="agent-cfo",
                )
            )

    async def test_pending_invite_allowed_after_prior_declined(
        self, backend: PersistenceBackend
    ) -> None:
        # The partial-unique index keys only on PENDING rows, so once a
        # prior invite for a target is DECLINED the slot is released: a
        # fresh PENDING invite for the same target must persist. This is
        # the whole point of the partial (vs full) unique constraint and
        # would silently break if it were ever widened to all statuses.
        await _seed_conversation(backend)
        repo = _repo(backend)
        first = _make_invite(
            invite_id="re-1", approval_id="a-re-1", target_agent_id="agent-cfo"
        )
        await repo.save(first)
        assert await repo.transition_if(
            first.id,
            from_state=ConversationInviteStatus.PENDING,
            to_state=ConversationInviteStatus.DECLINED,
        )
        # The same target can now be re-invited without colliding.
        await repo.save(
            _make_invite(
                invite_id="re-2", approval_id="a-re-2", target_agent_id="agent-cfo"
            )
        )
        assert await repo.get("re-2") is not None

    async def test_distinct_target_pending_invites_allowed(
        self, backend: PersistenceBackend
    ) -> None:
        # The partial-unique index keys on target_agent_id, so two
        # PENDING invites for DIFFERENT targets in one conversation both
        # persist (only the same-target collision is rejected).
        await _seed_conversation(backend)
        repo = _repo(backend)
        await repo.save(
            _make_invite(
                invite_id="dt-1", approval_id="a-dt-1", target_agent_id="agent-cfo"
            )
        )
        await repo.save(
            _make_invite(
                invite_id="dt-2", approval_id="a-dt-2", target_agent_id="agent-coo"
            )
        )
        assert await repo.get("dt-1") is not None
        assert await repo.get("dt-2") is not None

    async def test_transition_if_flips_state_atomically(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_conversation(backend)
        repo = _repo(backend)
        invite = _make_invite(invite_id="it-flip", approval_id="a-tf")
        await repo.save(invite)

        result = await repo.transition_if(
            invite.id,
            from_state=ConversationInviteStatus.PENDING,
            to_state=ConversationInviteStatus.ACCEPTED,
        )
        assert result is True
        fetched = await repo.get(invite.id)
        assert fetched is not None
        assert fetched.status is ConversationInviteStatus.ACCEPTED

    async def test_transition_if_returns_false_on_mismatch(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_conversation(backend)
        repo = _repo(backend)
        invite = _make_invite(
            invite_id="it-mismatch",
            approval_id="a-tm",
            status=ConversationInviteStatus.DECLINED,
        )
        await repo.save(invite)

        result = await repo.transition_if(
            invite.id,
            from_state=ConversationInviteStatus.PENDING,
            to_state=ConversationInviteStatus.ACCEPTED,
        )
        assert result is False

    async def test_delete_returns_true_then_false(
        self, backend: PersistenceBackend
    ) -> None:
        await _seed_conversation(backend)
        repo = _repo(backend)
        invite = _make_invite(invite_id="it-del", approval_id="a-del")
        await repo.save(invite)
        assert await repo.delete(invite.id) is True
        assert await repo.get(invite.id) is None
        assert await repo.delete(invite.id) is False

    async def test_protocol_runtime_check(self, backend: PersistenceBackend) -> None:
        assert isinstance(_repo(backend), ConversationInviteRepository)

    async def test_conversational_invite_source_check_per_backend(
        self, backend: PersistenceBackend
    ) -> None:  # lint-allow: dual-backend-parity -- source CHECK is asymmetric by design (Postgres admits, SQLite rejects)  # noqa: E501
        # The consent approval is stamped ``source=CONVERSATIONAL_INVITE``.
        # Postgres (the production backend) widened ``approvals.source``
        # to admit it, so it persists and round-trips. SQLite keeps the
        # narrow source domain on purpose -- conversational approvals stay
        # in-memory there (the proposer hard-blocks the persistent SQLite
        # + ApprovalStore combo), so the table must REJECT the row. Both
        # arms pin that deliberate divergence.
        repo = _approval_repo(backend)
        item = ApprovalItem(
            id=as_uuid("appr-invite"),
            action_type=NotBlankStr("conversational:invite_agent"),
            title=NotBlankStr("Invite Fiona into the conversation"),
            description=NotBlankStr("budget sign-off needed"),
            requested_by=NotBlankStr("agent-ceo"),
            risk_level=ApprovalRiskLevel.MEDIUM,
            source=ApprovalSource.CONVERSATIONAL_INVITE,
            status=ApprovalStatus.PENDING,
            created_at=_NOW,
        )
        if backend.backend_name == "postgres":
            await repo.save(item)
            fetched = await repo.get(str(item.id))
            assert fetched is not None
            assert fetched.source is ApprovalSource.CONVERSATIONAL_INVITE
        else:
            with pytest.raises(ConstraintViolationError):
                await repo.save(item)
