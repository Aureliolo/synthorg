# module-kind: service
"""In-process external-client CRUD facade."""

import asyncio
import copy
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    CLIENT_CREATED_VIA_MCP,
    CLIENT_DEACTIVATED_VIA_MCP,
)

logger = get_logger(__name__)


class _ClientRecord:
    """In-memory record of one external client."""

    __slots__ = (
        "active",
        "contact_email",
        "created_at",
        "id",
        "name",
        "notes",
        "satisfaction_score",
    )

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002
        name: str,
        contact_email: str | None,
        notes: str | None,
        created_at: datetime,
    ) -> None:
        self.id = id
        self.name = name
        self.contact_email = contact_email
        self.notes = notes
        self.created_at = created_at
        self.active = True
        self.satisfaction_score: float | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialise the client record to a JSON-safe dict.

        Returns:
            A dict of the client's ID, name, contact, notes, active flag,
            satisfaction score, and ISO-formatted creation timestamp.
        """
        return {
            "id": str(self.id),
            "name": self.name,
            "contact_email": self.contact_email,
            "notes": self.notes,
            "active": self.active,
            "satisfaction_score": self.satisfaction_score,
            "created_at": self.created_at.isoformat(),
        }


class ClientFacadeService:
    """In-process external-client CRUD facade.

    Mutations are serialised through a single :class:`asyncio.Lock` so
    concurrent MCP handler calls cannot race on the in-memory dict
    (notably the check-then-act in :meth:`deactivate_client`).
    """

    def __init__(self) -> None:
        self._clients: dict[UUID, _ClientRecord] = {}
        self._lock = asyncio.Lock()

    async def list_clients(self) -> Sequence[_ClientRecord]:
        """List external clients, newest-first.

        Returns:
            A tuple of deep-copied client records ordered by creation
            time (most recent first).
        """
        async with self._lock:
            snapshot = tuple(copy.deepcopy(c) for c in self._clients.values())
        return tuple(sorted(snapshot, key=lambda c: c.created_at, reverse=True))

    async def get_client(self, client_id: NotBlankStr) -> _ClientRecord | None:
        """Fetch a client by ID.

        Returns:
            A deep copy of the matching client record, or ``None`` when
            ``client_id`` is not a valid UUID or no client matches.
        """
        try:
            key = UUID(client_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._clients.get(key)
            return copy.deepcopy(record) if record is not None else None

    async def create_client(
        self,
        *,
        name: NotBlankStr,
        actor_id: NotBlankStr,
        contact_email: str | None = None,
        notes: str | None = None,
    ) -> _ClientRecord:
        """Create an external client, auditing the event.

        Returns:
            A deep copy of the newly created ``_ClientRecord``.
        """
        record = _ClientRecord(
            id=uuid4(),
            name=name,
            contact_email=contact_email,
            notes=notes,
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._clients[record.id] = record
        logger.info(
            CLIENT_CREATED_VIA_MCP,
            client_id=str(record.id),
            actor_id=actor_id,
        )
        return copy.deepcopy(record)

    async def deactivate_client(
        self,
        *,
        client_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Deactivate a client, auditing the event.

        Returns:
            ``True`` when the client was found and deactivated; ``False``
            when ``client_id`` is not a valid UUID or no client matches.
        """
        try:
            key = UUID(client_id)
        except ValueError:
            return False
        async with self._lock:
            record = self._clients.get(key)
            if record is None:
                return False
            record.active = False
            logger.info(
                CLIENT_DEACTIVATED_VIA_MCP,
                client_id=client_id,
                actor_id=actor_id,
                reason=reason,
            )
        return True

    async def get_satisfaction(
        self,
        client_id: NotBlankStr,
    ) -> Mapping[str, object]:
        """Return a client's satisfaction summary.

        Returns:
            A mapping with the client's ID, score, and active flag, or
            ``{"status": "unknown", "reason": "not_found"}`` when no
            client matches.
        """
        record = await self.get_client(client_id)
        if record is None:
            return {"status": "unknown", "reason": "not_found"}
        return {
            "client_id": str(record.id),
            "score": record.satisfaction_score,
            "active": record.active,
        }
