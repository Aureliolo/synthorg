# ruff: noqa: EM101, PLR0913
"""Integrations facades for the MCP handler layer.

MCPCatalogFacadeService, OAuthFacadeService, ClientFacadeService,
ArtifactFacadeService, and OntologyFacadeService.  Each wraps the
corresponding primitive on AppState (or an in-memory store when no
primitive yet exists) and raises
:class:`CapabilityNotSupportedError` for methods the primitive does
not yet implement.
"""

import asyncio
import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    ARTIFACT_CREATED_VIA_MCP,
    ARTIFACT_DELETED_VIA_MCP,
    CLIENT_CREATED_VIA_MCP,
    CLIENT_DEACTIVATED_VIA_MCP,
    MCP_CATALOG_INSTALLED_VIA_MCP,
    MCP_CATALOG_UNINSTALLED_VIA_MCP,
    OAUTH_PROVIDER_CONFIGURED_VIA_MCP,
    OAUTH_PROVIDER_REMOVED_VIA_MCP,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from synthorg.core.types import NotBlankStr
    from synthorg.integrations.mcp_catalog.installations import (
        McpInstallationRepository,
    )
    from synthorg.integrations.mcp_catalog.service import CatalogService
    from synthorg.integrations.oauth.token_manager import OAuthTokenManager
    from synthorg.ontology.service import OntologyService
    from synthorg.persistence.artifact_storage import ArtifactStorageBackend

logger = get_logger(__name__)


# ── MCPCatalogFacadeService ────────────────────────────────────────


class MCPCatalogFacadeService:
    """Facade over :class:`CatalogService` + installation repo."""

    def __init__(
        self,
        *,
        catalog: CatalogService,
        installations: McpInstallationRepository,
    ) -> None:
        self._catalog = cast("Any", catalog)
        self._installations = cast("Any", installations)

    async def list_catalog(self) -> Sequence[object]:
        """List all catalog entries.

        Returns:
            A tuple of all catalog entries.

        Raises:
            CapabilityNotSupportedError: If the backing ``CatalogService``
                does not expose ``list_entries``.
        """
        fn = getattr(self._catalog, "list_entries", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "mcp_catalog_list",
                "CatalogService does not expose list_entries",
            )
        return tuple(await fn())

    async def search_catalog(
        self,
        query: NotBlankStr,
    ) -> Sequence[object]:
        """Search catalog entries by query string.

        Returns:
            A tuple of catalog entries matching the query.

        Raises:
            CapabilityNotSupportedError: If the backing ``CatalogService``
                does not expose ``search``.
        """
        fn = getattr(self._catalog, "search", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "mcp_catalog_search",
                "CatalogService does not expose search",
            )
        return tuple(await fn(query))

    async def get_catalog_entry(
        self,
        entry_id: NotBlankStr,
    ) -> object | None:
        """Fetch a single catalog entry by ID.

        Returns:
            The matching catalog entry, or ``None`` when no entry has the
            given ID.

        Raises:
            CapabilityNotSupportedError: If the backing ``CatalogService``
                does not expose ``get_entry``.
        """
        fn = getattr(self._catalog, "get_entry", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "mcp_catalog_get",
                "CatalogService does not expose get_entry",
            )
        return cast("object | None", await fn(entry_id))

    async def install_catalog_entry(
        self,
        *,
        entry_id: NotBlankStr,
        actor_id: NotBlankStr,
    ) -> object:
        """Install a catalog entry and audit the event.

        Returns:
            The installation record returned by the repository.

        Raises:
            CapabilityNotSupportedError: If the installation repository
                does not expose ``install``.
        """
        fn = getattr(self._installations, "install", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "mcp_catalog_install",
                "McpInstallationRepository does not expose install",
            )
        result = await fn(entry_id=entry_id, actor=actor_id)
        logger.info(
            MCP_CATALOG_INSTALLED_VIA_MCP,
            entry_id=entry_id,
            actor_id=actor_id,
        )
        return result

    async def uninstall_catalog_entry(
        self,
        *,
        installation_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Uninstall a catalog entry and audit the event.

        Returns:
            ``True`` when an installation row was removed; ``False`` on a
            miss.

        Raises:
            CapabilityNotSupportedError: If the installation repository
                does not expose ``uninstall``.
        """
        fn = getattr(self._installations, "uninstall", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "mcp_catalog_uninstall",
                "McpInstallationRepository does not expose uninstall",
            )
        removed = bool(await fn(installation_id=installation_id))
        if removed:
            logger.info(
                MCP_CATALOG_UNINSTALLED_VIA_MCP,
                installation_id=installation_id,
                actor_id=actor_id,
                reason=reason,
            )
        return removed


# ── OAuthFacadeService ─────────────────────────────────────────────


class _OAuthProviderRecord:
    """In-memory record of one registered OAuth provider."""

    __slots__ = (
        "authorize_url",
        "client_id",
        "created_at",
        "name",
        "scopes",
        "token_url",
    )

    def __init__(
        self,
        *,
        name: str,
        client_id: str,
        authorize_url: str,
        token_url: str,
        scopes: tuple[str, ...],
        created_at: datetime,
    ) -> None:
        self.name = name
        self.client_id = client_id
        self.authorize_url = authorize_url
        self.token_url = token_url
        self.scopes = scopes
        self.created_at = created_at

    def to_dict(self) -> dict[str, object]:
        """Serialise the provider record to a JSON-safe dict.

        Returns:
            A dict of the provider's name, client ID, URLs, scopes, and
            ISO-formatted creation timestamp.
        """
        return {
            "name": self.name,
            "client_id": self.client_id,
            "authorize_url": self.authorize_url,
            "token_url": self.token_url,
            "scopes": list(self.scopes),
            "created_at": self.created_at.isoformat(),
        }


class OAuthFacadeService:
    """In-process OAuth provider registry.

    Mutations are serialised through a single :class:`asyncio.Lock` so
    concurrent MCP handler calls cannot race on the in-memory dict
    (check-then-act in :meth:`remove_provider`, unsynchronised writes
    in :meth:`configure_provider`).
    """

    def __init__(
        self,
        *,
        token_manager: OAuthTokenManager | None = None,
    ) -> None:
        self._token_manager = cast("Any", token_manager) if token_manager else None
        self._providers: dict[str, _OAuthProviderRecord] = {}
        self._lock = asyncio.Lock()

    async def list_providers(self) -> Sequence[_OAuthProviderRecord]:
        """List registered OAuth providers, newest-first.

        Returns:
            A tuple of deep-copied provider records ordered by creation
            time (most recent first).
        """
        async with self._lock:
            snapshot = tuple(copy.deepcopy(p) for p in self._providers.values())
        return tuple(sorted(snapshot, key=lambda p: p.created_at, reverse=True))

    async def configure_provider(
        self,
        *,
        name: NotBlankStr,
        client_id: NotBlankStr,
        authorize_url: NotBlankStr,
        token_url: NotBlankStr,
        scopes: Sequence[str],
        actor_id: NotBlankStr,
    ) -> _OAuthProviderRecord:
        """Register or overwrite an OAuth provider, auditing the event.

        Returns:
            A deep copy of the stored ``_OAuthProviderRecord``.
        """
        record = _OAuthProviderRecord(
            name=name,
            client_id=client_id,
            authorize_url=authorize_url,
            token_url=token_url,
            scopes=tuple(scopes),
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._providers[record.name] = record
        logger.info(
            OAUTH_PROVIDER_CONFIGURED_VIA_MCP,
            provider_name=name,
            actor_id=actor_id,
        )
        return copy.deepcopy(record)

    async def remove_provider(
        self,
        *,
        name: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Remove an OAuth provider, auditing on a successful removal.

        Returns:
            ``True`` when a provider was removed; ``False`` on a miss.
        """
        async with self._lock:
            removed = self._providers.pop(name, None) is not None
        if removed:
            logger.info(
                OAUTH_PROVIDER_REMOVED_VIA_MCP,
                provider_name=name,
                actor_id=actor_id,
                reason=reason,
                removed=removed,
            )
        return removed


# ── ClientFacadeService ─────────────────────────────────────────────


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


# ── ArtifactFacadeService ──────────────────────────────────────────


class _ArtifactRecord:
    """In-memory index entry for one stored artifact."""

    __slots__ = (
        "content_type",
        "created_at",
        "id",
        "name",
        "size_bytes",
        "storage_ref",
    )

    def __init__(
        self,
        *,
        id: UUID,  # noqa: A002
        name: str,
        content_type: str,
        size_bytes: int,
        storage_ref: str,
        created_at: datetime,
    ) -> None:
        self.id = id
        self.name = name
        self.content_type = content_type
        self.size_bytes = size_bytes
        self.storage_ref = storage_ref
        self.created_at = created_at

    def to_dict(self) -> dict[str, object]:
        """Serialise the artifact record to a JSON-safe dict.

        Returns:
            A dict of the artifact's ID, name, content type, size,
            storage ref, and ISO-formatted creation timestamp.
        """
        return {
            "id": str(self.id),
            "name": self.name,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "storage_ref": self.storage_ref,
            "created_at": self.created_at.isoformat(),
        }


class ArtifactFacadeService:
    """Facade over :class:`ArtifactStorageBackend`.

    Reads and writes wrap the storage primitive; listing and metadata
    lookups use an in-memory index populated on create so that MCP
    clients can browse artifacts without touching durable storage for
    every request.
    """

    def __init__(self, *, storage: ArtifactStorageBackend) -> None:
        self._storage = cast("Any", storage)
        self._index: dict[UUID, _ArtifactRecord] = {}
        self._lock = asyncio.Lock()

    async def list_artifacts(self) -> Sequence[_ArtifactRecord]:
        """List indexed artifacts, newest-first.

        Returns:
            A tuple of deep-copied artifact records ordered by creation
            time (most recent first).
        """
        async with self._lock:
            snapshot = tuple(copy.deepcopy(a) for a in self._index.values())
        return tuple(sorted(snapshot, key=lambda a: a.created_at, reverse=True))

    async def get_artifact(self, artifact_id: NotBlankStr) -> _ArtifactRecord | None:
        """Fetch an artifact's index record by ID.

        Returns:
            A deep copy of the matching record, or ``None`` when
            ``artifact_id`` is not a valid UUID or no record matches.
        """
        try:
            key = UUID(artifact_id)
        except ValueError:
            return None
        async with self._lock:
            record = self._index.get(key)
            return copy.deepcopy(record) if record is not None else None

    async def create_artifact(
        self,
        *,
        name: NotBlankStr,
        content_type: NotBlankStr,
        size_bytes: int,
        storage_ref: NotBlankStr,
        actor_id: NotBlankStr,
    ) -> _ArtifactRecord:
        """Index a stored artifact, auditing the event.

        Returns:
            A deep copy of the newly created ``_ArtifactRecord``.
        """
        record = _ArtifactRecord(
            id=uuid4(),
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_ref=storage_ref,
            created_at=datetime.now(UTC),
        )
        async with self._lock:
            self._index[record.id] = record
        logger.info(
            ARTIFACT_CREATED_VIA_MCP,
            artifact_id=str(record.id),
            actor_id=actor_id,
            size_bytes=size_bytes,
        )
        return copy.deepcopy(record)

    async def delete_artifact(
        self,
        *,
        artifact_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> bool:
        """Delete an artifact from storage and the index, auditing it.

        Returns:
            ``True`` when the artifact was removed from both storage and
            the index; ``False`` when ``artifact_id`` is not a valid
            UUID, no record matches, or the storage delete reports a miss.

        Raises:
            CapabilityNotSupportedError: If the storage backend does not
                expose ``delete`` (refusing to orphan the blob).
        """
        try:
            key = UUID(artifact_id)
        except ValueError:
            return False
        # Serialise the index read + storage delete + index pop so two
        # concurrent deletes of the same artifact cannot race: without
        # the lock, both coroutines could read the same record, both
        # call ``storage.delete`` (potentially raising for the second),
        # and only one ``pop`` would succeed while the other logs a
        # spurious success.
        async with self._lock:
            record = self._index.get(key)
            if record is None:
                return False
            fn = getattr(self._storage, "delete", None)
            if not callable(fn):
                raise CapabilityNotSupportedError(
                    "artifact_delete",
                    "ArtifactStorageBackend does not expose delete; refusing "
                    "to drop the index entry silently since the blob would be "
                    "orphaned.",
                )
            # Delete from storage FIRST so the index and storage cannot
            # diverge silently -- if storage fails, the record stays in
            # the index and the caller sees the real error.  Use the
            # backend's own storage_ref, not the facade UUID, because the
            # two diverge when the storage backend uses its own scheme.
            # Treat a falsy return (e.g. ``False`` for "not found" in the
            # backend) as an actual miss: don't drop the index entry or
            # log a fake success.
            storage_removed = await fn(record.storage_ref)
            # Any falsy return (``False``, ``None``, ``0``) is treated
            # as a miss so the index entry stays put and no audit
            # event fires; only a truthy confirmation drops the row.
            if not storage_removed:
                return False
            self._index.pop(key, None)
        logger.info(
            ARTIFACT_DELETED_VIA_MCP,
            artifact_id=artifact_id,
            actor_id=actor_id,
            reason=reason,
            removed=True,
        )
        return True


# ── OntologyFacadeService ──────────────────────────────────────────


class OntologyFacadeService:
    """Facade over :class:`OntologyService`."""

    def __init__(self, *, ontology: OntologyService) -> None:
        self._ontology = cast("Any", ontology)

    async def list_entities(self) -> Sequence[object]:
        """List all ontology entities.

        Returns:
            A tuple of all ontology entities.

        Raises:
            CapabilityNotSupportedError: If the backing ``OntologyService``
                does not expose ``list_entities``.
        """
        fn = getattr(self._ontology, "list_entities", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "ontology_list_entities",
                "OntologyService does not expose list_entities",
            )
        return tuple(await fn())

    async def get_entity(
        self,
        entity_id: NotBlankStr,
    ) -> object | None:
        """Fetch a single ontology entity by ID.

        Returns:
            The matching entity, or ``None`` when no entity has the given
            ID.

        Raises:
            CapabilityNotSupportedError: If the backing ``OntologyService``
                does not expose ``get_entity``.
        """
        fn = getattr(self._ontology, "get_entity", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "ontology_get_entity",
                "OntologyService does not expose get_entity",
            )
        return cast("object | None", await fn(entity_id))

    async def get_relationships(
        self,
        entity_id: NotBlankStr,
    ) -> Sequence[object]:
        """List an entity's relationships.

        Returns:
            A tuple of relationships for the given entity.

        Raises:
            CapabilityNotSupportedError: If the backing ``OntologyService``
                does not expose ``get_relationships``.
        """
        fn = getattr(self._ontology, "get_relationships", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "ontology_get_relationships",
                "OntologyService does not expose get_relationships",
            )
        return tuple(await fn(entity_id))

    async def search(
        self,
        query: NotBlankStr,
    ) -> Sequence[object]:
        """Search ontology entities by query string.

        Returns:
            A tuple of entities matching the query.

        Raises:
            CapabilityNotSupportedError: If the backing ``OntologyService``
                does not expose ``search``.
        """
        fn = getattr(self._ontology, "search", None)
        if not callable(fn):
            raise CapabilityNotSupportedError(
                "ontology_search",
                "OntologyService does not expose search",
            )
        return tuple(await fn(query))


__all__ = [
    "ArtifactFacadeService",
    "ClientFacadeService",
    "MCPCatalogFacadeService",
    "OAuthFacadeService",
    "OntologyFacadeService",
]
