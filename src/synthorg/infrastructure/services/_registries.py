# module-kind: service
"""In-process registries backing the project / request / template MCP tools.

Each registry is intentionally process-local: records created here live
for the lifetime of the app instance and back the MCP tools' read/write
surface.  Mutations are serialised through a single :class:`asyncio.Lock`
so concurrent MCP handler calls cannot race on the in-memory dict.
"""

import asyncio
import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from synthorg.observability import get_logger
from synthorg.observability.events.infrastructure import (
    PROJECT_CREATED_VIA_MCP,
    PROJECT_DELETED_VIA_MCP,
    PROJECT_UPDATED_VIA_MCP,
    REQUEST_CREATED_VIA_MCP,
)
from synthorg.observability.events.template import (
    TEMPLATE_PACK_INSTALLED_VIA_MCP,
    TEMPLATE_PACK_UNINSTALLED_VIA_MCP,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class _ProjectRecord:
    __slots__ = ("created_at", "description", "id", "metadata", "name")

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002
        name: str,
        description: str,
        created_at: datetime,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.description = description
        self.created_at = created_at
        self.metadata = dict(metadata or {})

    def to_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "metadata": dict(self.metadata),
        }


class ProjectFacadeService:
    """In-process project CRUD facade.

    Mutations are serialised through a single :class:`asyncio.Lock` so
    concurrent MCP handler calls cannot race on the in-memory dict
    (check-then-act in :meth:`update_project` and :meth:`delete_project`).
    """

    def __init__(self) -> None:
        self._projects: dict[UUID, _ProjectRecord] = {}
        self._lock = asyncio.Lock()

    async def list_projects(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[_ProjectRecord, ...], int]:
        """Return paginated projects newest-first plus the unfiltered total.

        Returns:
            A ``(page, total)`` pair: the deep-copied project records for
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
            snapshot = tuple(copy.deepcopy(p) for p in self._projects.values())
        ordered = tuple(
            sorted(snapshot, key=lambda p: p.created_at, reverse=True),
        )
        total = len(ordered)
        end = total if limit is None else offset + limit
        return ordered[offset:end], total

    async def get_project(self, project_id: NotBlankStr) -> _ProjectRecord | None:
        """Fetch a project by UUID or ``None`` if absent.

        Returns:
            A deep copy of the stored project, or ``None`` when the id is
            malformed or no such project exists.
        """
        try:
            key = UUID(project_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._projects.get(key)
            return copy.deepcopy(record) if record is not None else None

    async def create_project(
        self,
        *,
        name: NotBlankStr,
        description: NotBlankStr,
        actor_id: NotBlankStr,
        metadata: Mapping[str, str] | None = None,
    ) -> _ProjectRecord:
        """Create a project, auditing the event on success.

        Returns:
            A deep copy of the newly created project record.
        """
        record = _ProjectRecord(
            id=uuid4(),
            name=name,
            description=description,
            created_at=datetime.now(UTC),
            metadata=metadata,
        )
        async with self._lock:
            self._projects[record.id] = record
        logger.info(
            PROJECT_CREATED_VIA_MCP,
            project_id=str(record.id),
            actor_id=actor_id,
        )
        return copy.deepcopy(record)

    async def update_project(
        self,
        *,
        project_id: NotBlankStr,
        actor_id: NotBlankStr,
        name: NotBlankStr | None = None,
        description: NotBlankStr | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> _ProjectRecord | None:
        """Apply overrides via copy-on-write; return ``None`` if project missing.

        Returns:
            A deep copy of the refreshed project, or ``None`` when the id
            is malformed or no such project exists.
        """
        try:
            key = UUID(project_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._projects.get(key)
            if record is None:
                return None
            # Copy-on-write: build a new ``_ProjectRecord`` with the
            # overrides applied and replace the dict entry, rather
            # than mutating the stored object in place.  That keeps
            # any deepcopy returned from a prior ``get``/``list`` call
            # decoupled from subsequent updates.
            refreshed = _ProjectRecord(
                id=record.id,
                name=name if name is not None else record.name,
                description=(
                    description if description is not None else record.description
                ),
                created_at=record.created_at,
                metadata=(
                    dict(metadata) if metadata is not None else dict(record.metadata)
                ),
            )
            self._projects[key] = refreshed
            returned = copy.deepcopy(refreshed)
        logger.info(
            PROJECT_UPDATED_VIA_MCP,
            project_id=project_id,
            actor_id=actor_id,
        )
        return returned

    async def delete_project(
        self,
        *,
        project_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Remove a project; only audit when a row was actually dropped.

        Returns:
            ``True`` when a project was removed, ``False`` when the id is
            malformed or no such project exists.
        """
        try:
            key = UUID(project_id)
        except ValueError:
            return False
        async with self._lock:
            removed = self._projects.pop(key, None) is not None
        if removed:
            logger.info(
                PROJECT_DELETED_VIA_MCP,
                project_id=project_id,
                actor_id=actor_id,
                reason=reason,
                removed=removed,
            )
        return removed


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
