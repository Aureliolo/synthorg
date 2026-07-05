# module-kind: tests
"""Shared in-memory conversation repository doubles.

One canonical set of ``ConversationRepository`` / ``ConversationTurnRepository``
/ ``ConversationalProposalRepository`` doubles for every suite that drives the
conversational-org persistence seam (the proposer suites, the charter service
suite, the dispatch suite). Consolidated so a protocol change (a new filter
kwarg, a new method) updates one place instead of silently drifting across
per-file copies -- the exact drift that lets a fake fall behind the protocol
under typeguard's whole-protocol check.
"""

from datetime import datetime

from synthorg.communication.conversation.enums import (
    ConversationalProposalStatus,
    ConversationStatus,
)
from synthorg.meta.chief_of_staff.models import (
    Conversation,
    ConversationalProposal,
    ConversationTurn,
)
from synthorg.persistence.conversation_protocol import ConversationTurnFilterSpec
from synthorg.persistence.conversational_proposal_protocol import (
    ConversationalProposalFilterSpec,
)


class FakeConversationRepo:
    """In-memory ``ConversationRepository`` double."""

    def __init__(self) -> None:
        self.items: dict[str, Conversation] = {}

    async def save(self, entity: Conversation) -> None:
        self.items[str(entity.id)] = entity

    async def get(self, entity_id: str) -> Conversation | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self,
        *,
        created_by: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[Conversation, ...]:
        rows = [
            c
            for c in self.items.values()
            if created_by is None or c.created_by == created_by
        ]
        # Match the real repositories' newest-first ordering
        # (``ORDER BY created_at DESC, id DESC``) so pagination behaviour
        # in tests reflects production rather than dict insertion order.
        rows.sort(key=lambda c: (c.created_at, str(c.id)), reverse=True)
        return tuple(rows[offset : offset + limit])

    async def transition_if(
        self,
        entity_id: str,
        from_state: ConversationStatus,
        to_state: ConversationStatus,
        **updates: object,
    ) -> bool:
        # Mirror the real ConversationRepository contract: ``updated_at``
        # (an ISO-8601 string) is the only supported update key. Reject any
        # other key rather than silently dropping it, matching the group-chat
        # participant/invite doubles' strictness.
        unexpected = set(updates) - {"updated_at"}
        if unexpected:
            msg = (
                "conversation transition_if got unsupported update keys: "
                f"{sorted(unexpected)}"
            )
            raise ValueError(msg)
        current = self.items.get(entity_id)
        if current is None or current.status is not from_state:
            return False
        changes: dict[str, object] = {"status": to_state}
        raw_updated_at = updates.get("updated_at")
        if raw_updated_at is not None:
            changes["updated_at"] = datetime.fromisoformat(str(raw_updated_at))
        self.items[entity_id] = current.model_copy(update=changes)
        return True


class FakeTurnRepo:
    """In-memory append-only ``ConversationTurnRepository`` double."""

    def __init__(self) -> None:
        self.turns: list[ConversationTurn] = []

    async def append(self, event: ConversationTurn) -> None:
        self.turns.append(event)

    async def query(
        self,
        filter_spec: ConversationTurnFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ConversationTurn, ...]:
        rows = [
            t
            for t in self.turns
            if filter_spec.conversation_id is None
            or t.conversation_id == filter_spec.conversation_id
        ]
        rows.sort(key=lambda t: t.sequence, reverse=True)
        return tuple(rows[offset : offset + limit])

    async def purge_before(self, threshold: datetime) -> int:
        before = len(self.turns)
        self.turns = [t for t in self.turns if t.created_at >= threshold]
        return before - len(self.turns)


class FakeProposalRepo:
    """In-memory ``ConversationalProposalRepository`` double."""

    def __init__(self) -> None:
        self.items: dict[str, ConversationalProposal] = {}

    async def save(self, entity: ConversationalProposal) -> None:
        self.items[str(entity.id)] = entity

    async def get(self, entity_id: str) -> ConversationalProposal | None:
        return self.items.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self.items.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[ConversationalProposal, ...]:
        return tuple(self.items.values())[offset : offset + limit]

    async def transition_if(
        self,
        entity_id: str,
        from_state: ConversationalProposalStatus,
        to_state: ConversationalProposalStatus,
        **updates: object,
    ) -> bool:
        current = self.items.get(entity_id)
        if current is None or current.status is not from_state:
            return False
        self.items[entity_id] = current.model_copy(update={"status": to_state})
        return True

    async def query(
        self,
        filter_spec: ConversationalProposalFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ConversationalProposal, ...]:
        rows = [
            p
            for p in self.items.values()
            if (
                filter_spec.approval_id is None
                or p.approval_id == filter_spec.approval_id
            )
            and (
                filter_spec.conversation_id is None
                or p.conversation_id == filter_spec.conversation_id
            )
            and (filter_spec.status is None or p.status is filter_spec.status)
        ]
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: ConversationalProposalFilterSpec) -> int:
        return len(await self.query(filter_spec))


__all__ = ["FakeConversationRepo", "FakeProposalRepo", "FakeTurnRepo"]
