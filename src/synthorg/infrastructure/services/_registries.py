# module-kind: service
"""In-process registries backing the request / template MCP tools.

Each registry is intentionally process-local: records created here live
for the lifetime of the app instance and back the MCP tools' read/write
surface.  Mutations are serialised through a single :class:`asyncio.Lock`
so concurrent MCP handler calls cannot race on the in-memory dict.

Projects are deliberately NOT among them any more. A project is a durable
entity an operator opens, watches and deletes, so a process-local answer about
one is a different store wearing the same name: the tools reported an empty
list to an organisation that had projects, and reported a deletion that removed
nothing. Those tools now go through the same repository the dashboard reads.
"""

import asyncio
import copy
from datetime import UTC, datetime
from uuid import UUID, uuid4

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.infrastructure import REQUEST_CREATED_VIA_MCP
from synthorg.observability.events.template import (
    TEMPLATE_PACK_INSTALLED_VIA_MCP,
    TEMPLATE_PACK_UNINSTALLED_VIA_MCP,
)

logger = get_logger(__name__)


class _RequestRecord:
    __slots__ = ("body", "created_at", "id", "requested_by", "title")

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002
        title: str,
        body: str,
        requested_by: str,
        created_at: datetime,
    ) -> None:
        self.id = id
        self.title = title
        self.body = body
        self.requested_by = requested_by
        self.created_at = created_at

    def to_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "title": self.title,
            "body": self.body,
            "requested_by": self.requested_by,
            "created_at": self.created_at.isoformat(),
        }


class RequestsFacadeService:
    """In-process operator-request facade.

    Mutations are serialised through a single :class:`asyncio.Lock` so
    concurrent MCP handler calls cannot race on the in-memory dict.
    """

    def __init__(self) -> None:
        self._requests: dict[UUID, _RequestRecord] = {}
        self._lock = asyncio.Lock()

    async def list_requests(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[_RequestRecord, ...], int]:
        """Return paginated requests newest-first plus the unfiltered total.

        Returns:
            A ``(page, total)`` pair: the deep-copied request records for
            the requested slice, newest-first, and the unfiltered count.

        Raises:
            ValueError: If ``offset`` is negative, or ``limit`` is
                provided and non-positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        async with self._lock:
            snapshot = tuple(copy.deepcopy(r) for r in self._requests.values())
        ordered = tuple(
            sorted(snapshot, key=lambda r: r.created_at, reverse=True),
        )
        total = len(ordered)
        end = total if limit is None else offset + limit
        return ordered[offset:end], total

    async def get_request(self, request_id: NotBlankStr) -> _RequestRecord | None:
        """Fetch a request record by UUID or ``None`` if absent.

        Returns:
            A deep copy of the stored request, or ``None`` when the id is
            malformed or no such request exists.
        """
        try:
            key = UUID(request_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._requests.get(key)
            return copy.deepcopy(record) if record is not None else None

    async def create_request(
        self,
        *,
        title: NotBlankStr,
        body: NotBlankStr,
        requested_by: NotBlankStr,
    ) -> _RequestRecord:
        """Create a ledger request, auditing the event on success.

        Returns:
            A deep copy of the newly created request record.
        """
        record = _RequestRecord(
            id=uuid4(),
            title=title,
            body=body,
            requested_by=requested_by,
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._requests[record.id] = record
        logger.info(
            REQUEST_CREATED_VIA_MCP,
            request_id=str(record.id),
            requested_by=requested_by,
        )
        return copy.deepcopy(record)


class _TemplatePackRecord:
    __slots__ = ("id", "installed_at", "name", "version")

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002
        name: str,
        version: str,
        installed_at: datetime,
    ) -> None:
        self.id = id
        self.name = name
        self.version = version
        self.installed_at = installed_at

    def to_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "name": self.name,
            "version": self.version,
            "installed_at": self.installed_at.isoformat(),
        }


class TemplatePackFacadeService:
    """In-process template-pack registry.

    Mutations are serialised through a single :class:`asyncio.Lock` so
    concurrent MCP handler calls cannot race on the in-memory dict.
    """

    def __init__(self) -> None:
        self._packs: dict[UUID, _TemplatePackRecord] = {}
        self._lock = asyncio.Lock()

    async def list_packs(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[_TemplatePackRecord, ...], int]:
        """Return paginated packs newest-first plus the unfiltered total.

        Returns:
            A ``(page, total)`` pair: the deep-copied pack records for the
            requested slice, newest-first, and the unfiltered count.

        Raises:
            ValueError: If ``offset`` is negative, or ``limit`` is
                provided and non-positive.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        async with self._lock:
            snapshot = tuple(copy.deepcopy(p) for p in self._packs.values())
        ordered = tuple(
            sorted(snapshot, key=lambda p: p.installed_at, reverse=True),
        )
        total = len(ordered)
        end = total if limit is None else offset + limit
        return ordered[offset:end], total

    async def get_pack(self, pack_id: NotBlankStr) -> _TemplatePackRecord | None:
        """Fetch an installed pack by UUID or ``None`` if absent.

        Returns:
            A deep copy of the stored pack, or ``None`` when the id is
            malformed or no such pack is installed.
        """
        try:
            key = UUID(pack_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._packs.get(key)
            return copy.deepcopy(record) if record is not None else None

    async def install_pack(
        self,
        *,
        name: NotBlankStr,
        version: NotBlankStr,
        actor_id: NotBlankStr,
    ) -> _TemplatePackRecord:
        """Install a template pack, auditing the event on success.

        Returns:
            A deep copy of the newly installed pack record.
        """
        record = _TemplatePackRecord(
            id=uuid4(),
            name=name,
            version=version,
            installed_at=datetime.now(UTC),
        )
        async with self._lock:
            self._packs[record.id] = record
        logger.info(
            TEMPLATE_PACK_INSTALLED_VIA_MCP,
            pack_id=str(record.id),
            pack_name=name,
            actor_id=actor_id,
        )
        return copy.deepcopy(record)

    async def uninstall_pack(
        self,
        *,
        pack_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Remove a pack; only emit the audit event on actual removal.

        Returns:
            ``True`` when a pack was removed, ``False`` when the id is
            malformed or no such pack is installed.
        """
        try:
            key = UUID(pack_id)
        except ValueError:
            return False
        async with self._lock:
            removed = self._packs.pop(key, None) is not None
        if removed:
            logger.info(
                TEMPLATE_PACK_UNINSTALLED_VIA_MCP,
                pack_id=pack_id,
                actor_id=actor_id,
                reason=reason,
                removed=removed,
            )
        return removed
