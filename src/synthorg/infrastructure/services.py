# ruff: noqa: EM101, E501
"""Infrastructure facades for the MCP handler layer.

Thin per-subdomain facades used by the infrastructure MCP tools
(settings, providers, backup, users, projects, requests, setup,
simulations, template packs, audit, events, integration health).
Each facade wraps the already-attached AppState primitive and lifts
the common audit-log pattern into a single owner.

For operations whose underlying primitive does not yet expose the
required method the facade raises
:class:`~synthorg.communication.mcp_errors.CapabilityNotSupportedError`,
which the MCP handler translates to a typed ``not_supported`` envelope.
This keeps the distinction between "handler wired, primitive
capability missing" and "handler unwired" observable on the wire.

Primitives are stored internally as :class:`Any` so the facade can
introspect capabilities at runtime (``getattr`` + ``callable``
checks) without fighting protocol-type narrowing when the primitive
is still evolving.

The file-level ``EM101`` / ``E501`` suppressions are intentional:
capability-gap messages are string literals wired through
:func:`_capability_missing`, and the long-form capability descriptions
read better on one line for grep-ability than broken across multiple.
"""

import asyncio
import copy
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.observability import get_logger
from synthorg.observability.events.backup import (
    BACKUP_DELETED_VIA_MCP,
    BACKUP_RESTORE_TRIGGERED_VIA_MCP,
)
from synthorg.observability.events.infrastructure import (
    PROJECT_CREATED_VIA_MCP,
    PROJECT_DELETED_VIA_MCP,
    PROJECT_UPDATED_VIA_MCP,
    REQUEST_CREATED_VIA_MCP,
)
from synthorg.observability.events.settings import (
    SETTINGS_VALUE_DELETED,
    SETTINGS_VALUE_SET,
)
from synthorg.observability.events.template import (
    TEMPLATE_PACK_INSTALLED_VIA_MCP,
    TEMPLATE_PACK_UNINSTALLED_VIA_MCP,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from synthorg.api.auth.service import AuthService
    from synthorg.backup.service import BackupService as CoreBackupService
    from synthorg.client.simulation_state import ClientSimulationState
    from synthorg.communication.event_stream.stream import EventStreamHub
    from synthorg.core.types import NotBlankStr
    from synthorg.integrations.health.prober import HealthProberService
    from synthorg.providers.health import ProviderHealthTracker
    from synthorg.providers.management.service import ProviderManagementService
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.security.audit import AuditLog
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


def _capability_missing(
    capability: str,
    detail: str,
) -> CapabilityNotSupportedError:
    """Shared helper for typed capability-gap errors."""
    return CapabilityNotSupportedError(capability, detail)


def _require_callable(
    target: Any,
    method_name: str,
    capability: str,
    detail: str,
) -> Any:
    """Return a callable attribute or raise ``CapabilityNotSupportedError``."""
    fn = getattr(target, method_name, None)
    if not callable(fn):
        raise _capability_missing(capability, detail)
    return fn


def _split_setting_key(key: str) -> tuple[str, str]:
    """Split a compound ``namespace.key`` wire identifier into its parts.

    The MCP wire-level ``synthorg_settings_*`` tools accept a single
    ``key`` argument; :class:`SettingsService` takes ``namespace`` and
    ``key`` as distinct values.  This helper enforces the boundary and
    surfaces a typed capability error when the caller omits the
    namespace (no dot present).
    """
    namespace, dot, leaf = key.partition(".")
    if not dot or not namespace or not leaf:
        raise _capability_missing(
            "settings_key_format",
            f"setting key must be 'namespace.key' (got {key!r})",
        )
    return namespace, leaf


# ── SettingsReadService ──────────────────────────────────────────────


class SettingsReadService:
    """Facade over :class:`SettingsService` for MCP."""

    def __init__(self, *, settings: SettingsService) -> None:
        self._settings = cast("Any", settings)

    async def list_settings(self) -> Mapping[str, object]:
        """Return a mapping of all resolved settings keyed by ``namespace.key``."""
        fn = _require_callable(
            self._settings,
            "get_all",
            "settings_list",
            "SettingsService does not expose get_all()",
        )
        entries = await fn()
        return {
            f"{e.definition.namespace}.{e.definition.key}": e.value for e in entries
        }

    async def get_setting(self, key: NotBlankStr) -> object | None:
        """Return the resolved value for ``namespace.key`` or ``None`` if absent."""
        return (await self.list_settings()).get(key)

    async def update_setting(
        self,
        *,
        key: NotBlankStr,
        value: object,
        actor_id: NotBlankStr,
    ) -> None:
        """Write ``namespace.key``/``value`` and emit the audit event.

        ``key`` is the compound form ``<namespace>.<key>`` used on the
        MCP wire; the underlying :class:`SettingsService.set` signature
        takes ``namespace`` and ``key`` as separate positional args.
        """
        namespace, leaf_key = _split_setting_key(key)
        fn = _require_callable(
            self._settings,
            "set",
            "settings_update",
            "SettingsService does not expose a mutator",
        )
        # ``SettingsService.set`` expects a string value; JSON-typed
        # inputs on the MCP wire (bool, int, list, dict) must
        # round-trip through ``json.dumps`` so downstream validators
        # see the canonical form (``"true"``, ``"[1,2]"``) rather than
        # Python-repr (``"True"``, ``"[1, 2]"``).
        encoded = value if isinstance(value, str) else json.dumps(value)
        await fn(namespace, leaf_key, encoded)
        logger.info(SETTINGS_VALUE_SET, key=key, actor_id=actor_id)

    async def delete_setting(
        self,
        *,
        key: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> None:
        """Remove ``namespace.key`` from the store and emit the audit event."""
        namespace, leaf_key = _split_setting_key(key)
        fn = _require_callable(
            self._settings,
            "delete",
            "settings_delete",
            "SettingsService does not expose delete",
        )
        await fn(namespace, leaf_key)
        logger.info(
            SETTINGS_VALUE_DELETED,
            key=key,
            actor_id=actor_id,
            reason=reason,
        )


# ── ProviderReadService ──────────────────────────────────────────────


class ProviderReadService:
    """Facade over provider registry + health tracker + management."""

    def __init__(
        self,
        *,
        registry: ProviderRegistry,
        health: ProviderHealthTracker,
        management: ProviderManagementService,
    ) -> None:
        self._registry = cast("Any", registry)
        self._health = cast("Any", health)
        self._management = cast("Any", management)

    async def list_providers(self) -> Sequence[object]:
        """Return every registered provider record."""
        fn = _require_callable(
            self._registry,
            "list_providers",
            "provider_list",
            "ProviderRegistry does not expose list_providers",
        )
        return tuple(fn())

    async def get_provider(self, provider_id: NotBlankStr) -> object | None:
        """Return the provider record for ``provider_id`` or ``None``."""
        fn = _require_callable(
            self._registry,
            "get_provider",
            "provider_get",
            "ProviderRegistry does not expose get_provider",
        )
        return cast("object | None", fn(provider_id))

    async def get_health(
        self,
        provider_id: NotBlankStr | None = None,
    ) -> Mapping[str, object]:
        """Return health status for one provider or every registered provider."""
        status_fn = _require_callable(
            self._health,
            "get_status",
            "provider_health",
            "ProviderHealthTracker does not expose get_status",
        )
        if provider_id is None:
            ids_fn = _require_callable(
                self._registry,
                "list_provider_ids",
                "provider_health",
                "ProviderRegistry does not expose list_provider_ids",
            )
            return {pid: status_fn(pid) for pid in ids_fn()}
        return {provider_id: status_fn(provider_id)}

    async def test_connection(
        self,
        provider_id: NotBlankStr,
    ) -> Mapping[str, object]:
        """Run a connectivity test against ``provider_id`` and return its result."""
        fn = _require_callable(
            self._management,
            "test_provider",
            "provider_test",
            "ProviderManagementService does not expose test_provider",
        )
        return {"provider_id": provider_id, "result": await fn(provider_id)}


# ── BackupFacadeService ──────────────────────────────────────────────


class BackupFacadeService:
    """Facade wrapping :class:`BackupService`."""

    def __init__(self, *, service: CoreBackupService) -> None:
        self._service = cast("Any", service)

    async def list_backups(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[object, ...], int]:
        """Return paginated backups plus the unfiltered total.

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
        all_backups = tuple(await self._service.list_backups())
        total = len(all_backups)
        end = total if limit is None else offset + limit
        return all_backups[offset:end], total

    async def get_backup(self, backup_id: NotBlankStr) -> object:
        """Fetch a single backup by id."""
        return await self._service.get_backup(backup_id)

    async def create_backup(
        self,
        *,
        trigger: object,
        components: object = None,
    ) -> object:
        """Request a new backup for the given ``trigger``/``components``."""
        return await self._service.create_backup(trigger=trigger, components=components)

    async def delete_backup(
        self,
        *,
        backup_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> None:
        """Delete ``backup_id`` and emit the audit event on success."""
        await self._service.delete_backup(backup_id)
        logger.info(
            BACKUP_DELETED_VIA_MCP,
            backup_id=backup_id,
            actor_id=actor_id,
            reason=reason,
        )

    async def restore_backup(
        self,
        *,
        backup_id: NotBlankStr,
        actor_id: NotBlankStr,
        reason: NotBlankStr,
    ) -> Mapping[str, object]:
        """Trigger a restore from ``backup_id`` and emit the audit event."""
        await self._service.restore_from_backup(backup_id)
        logger.info(
            BACKUP_RESTORE_TRIGGERED_VIA_MCP,
            backup_id=backup_id,
            actor_id=actor_id,
            reason=reason,
        )
        return {"backup_id": backup_id, "restored": True}


# ── UserService ──────────────────────────────────────────────────────


class UserFacadeService:
    """Facade over :class:`AuthService` for user CRUD."""

    def __init__(self, *, auth_service: AuthService) -> None:
        self._auth = cast("Any", auth_service)

    async def list_users(self) -> Sequence[object]:
        """Return every user record exposed by the auth service."""
        fn = _require_callable(
            self._auth,
            "list_users",
            "user_list",
            "AuthService does not expose list_users; populate via durable user repository",
        )
        return tuple(await fn())

    async def get_user(self, user_id: NotBlankStr) -> object | None:
        """Return the user record for ``user_id`` or ``None`` if absent."""
        fn = _require_callable(
            self._auth,
            "get_user",
            "user_get",
            "AuthService does not expose get_user",
        )
        return cast("object | None", await fn(user_id))

    async def create_user(
        self,
        *,
        username: NotBlankStr,  # noqa: ARG002 - part of public contract
        role: NotBlankStr,  # noqa: ARG002
        actor_id: NotBlankStr,  # noqa: ARG002
    ) -> None:
        """Capability gap -- user onboarding flow owns user creation."""
        raise _capability_missing(
            "user_create",
            "users are provisioned via the onboarding flow, not MCP",
        )

    async def update_user(
        self,
        *,
        user_id: NotBlankStr,  # noqa: ARG002 - part of public contract
        updates: Mapping[str, object],  # noqa: ARG002
        actor_id: NotBlankStr,  # noqa: ARG002
    ) -> None:
        """Capability gap -- auth controller owns user mutations."""
        raise _capability_missing(
            "user_update",
            "user mutations go through the auth controller, not MCP",
        )

    async def delete_user(
        self,
        *,
        user_id: NotBlankStr,  # noqa: ARG002 - part of public contract
        actor_id: NotBlankStr,  # noqa: ARG002
        reason: NotBlankStr,  # noqa: ARG002
    ) -> None:
        """Capability gap -- deletion flows through the operator workflow."""
        raise _capability_missing(
            "user_delete",
            "user deletion is a protected operator workflow",
        )


# ── ProjectService ──────────────────────────────────────────────────


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
        """Fetch a project by UUID or ``None`` if absent."""
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
        """Create a project, auditing the event on success."""
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
        """Apply overrides via copy-on-write; return ``None`` if project missing."""
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
        """Remove a project; only audit when a row was actually dropped."""
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


# ── RequestsService ─────────────────────────────────────────────────


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
        """Fetch a request record by UUID or ``None`` if absent."""
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
        """Create a ledger request, auditing the event on success."""
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


# ── SetupService ────────────────────────────────────────────────────


class SetupFacadeService:
    """Setup status + initialisation facade."""

    def __init__(self) -> None:
        self._initialised_at: datetime | None = None

    async def get_status(self) -> Mapping[str, object]:
        """Return the initialisation status + timestamp (when initialised)."""
        return {
            "initialised": self._initialised_at is not None,
            "initialised_at": (
                self._initialised_at.isoformat()
                if self._initialised_at is not None
                else None
            ),
        }

    async def initialize(
        self,
        *,
        config: Mapping[str, object],  # noqa: ARG002 - part of public contract
    ) -> None:
        """Capability gap -- setup runs through the controller + CLI wizard."""
        raise _capability_missing(
            "setup_initialize",
            "initialisation is driven through the setup controller + CLI wizard",
        )


# ── SimulationService ──────────────────────────────────────────────


class SimulationFacadeService:
    """Facade over :class:`ClientSimulationState`."""

    def __init__(self, *, state: ClientSimulationState) -> None:
        self._state = cast("Any", state)

    async def list_simulations(
        self,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[object, ...], int]:
        """Return paginated simulation scenarios plus the unfiltered total.

        Raises:
            ValueError: If ``offset`` is negative, or ``limit`` is
                provided and non-positive.
            CapabilityNotSupportedError: If the state object does not
                expose ``list_scenarios``.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = f"limit must be >= 1 when provided, got {limit}"
            raise ValueError(msg)
        fn = _require_callable(
            self._state,
            "list_scenarios",
            "simulation_list",
            "ClientSimulationState does not expose list_scenarios",
        )
        all_scenarios = tuple(fn())
        total = len(all_scenarios)
        end = total if limit is None else offset + limit
        return all_scenarios[offset:end], total

    async def get_simulation(self, simulation_id: NotBlankStr) -> object | None:
        """Fetch a scenario by id or ``None`` if absent."""
        fn = _require_callable(
            self._state,
            "get_scenario",
            "simulation_get",
            "ClientSimulationState does not expose get_scenario",
        )
        return cast("object | None", fn(simulation_id))

    async def create_simulation(self) -> None:
        """Capability gap -- scenarios are loaded from config at start-up."""
        raise _capability_missing(
            "simulation_create",
            "simulation scenarios are loaded from config at start-up",
        )


# ── TemplatePackService ────────────────────────────────────────────


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
        """Fetch an installed pack by UUID or ``None`` if absent."""
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
        """Install a template pack, auditing the event on success."""
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
        """Remove a pack; only emit the audit event on actual removal."""
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


# ── AuditReadService ────────────────────────────────────────────────


class AuditReadService:
    """Read facade over :class:`AuditLog`."""

    def __init__(self, *, audit_log: AuditLog) -> None:
        self._audit = cast("Any", audit_log)

    async def list_entries(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[tuple[object, ...], int]:
        """Return paginated audit entries plus the unfiltered total.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` < 1.
            CapabilityNotSupportedError: If the underlying
                :class:`AuditLog` does not expose ``list_entries``.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            raise ValueError(msg)
        fn = getattr(self._audit, "list_entries", None)
        if not callable(fn):
            raise _capability_missing(
                "audit_list",
                "AuditLog does not expose list_entries",
            )
        all_entries = tuple(fn())
        total = len(all_entries)
        page = all_entries[offset : offset + limit]
        return page, total


# ── EventsReadService ──────────────────────────────────────────────


class EventsReadService:
    """Read facade over :class:`EventStreamHub`."""

    def __init__(self, *, hub: EventStreamHub) -> None:
        self._hub = cast("Any", hub)

    async def list_events(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[tuple[object, ...], int]:
        """Return paginated recent events plus the unfiltered total.

        Fetches the hub's full retained buffer so ``total`` reflects
        the true count (not just ``offset + limit``), then slices the
        requested window.

        Raises:
            ValueError: If ``offset`` is negative or ``limit`` < 1.
            CapabilityNotSupportedError: If the underlying
                :class:`EventStreamHub` does not expose ``recent_events``.
        """
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            raise ValueError(msg)
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            raise ValueError(msg)
        fn = getattr(self._hub, "recent_events", None)
        if not callable(fn):
            raise _capability_missing(
                "events_list",
                "EventStreamHub does not expose recent_events",
            )
        # Ask for every retained event so ``total`` is the hub's full
        # retention count rather than the pagination window size.  The
        # hub keeps a bounded ring buffer so this is not unbounded.
        all_events = tuple(fn())
        total = len(all_events)
        page = all_events[offset : offset + limit]
        return page, total


# ── IntegrationHealthService ──────────────────────────────────────


class IntegrationHealthFacadeService:
    """Read facade over :class:`HealthProberService`."""

    def __init__(self, *, prober: HealthProberService) -> None:
        self._prober = cast("Any", prober)

    async def get_all(self) -> Mapping[str, object]:
        """Return the full integration-health snapshot."""
        fn = getattr(self._prober, "snapshot", None)
        if not callable(fn):
            raise _capability_missing(
                "integration_health_list",
                "HealthProberService does not expose snapshot",
            )
        return dict(fn())

    async def get_one(self, integration_id: NotBlankStr) -> object | None:
        """Return the health entry for ``integration_id`` or ``None``."""
        snapshot = await self.get_all()
        return snapshot.get(integration_id)


__all__ = [
    "AuditReadService",
    "BackupFacadeService",
    "EventsReadService",
    "IntegrationHealthFacadeService",
    "ProjectFacadeService",
    "ProviderReadService",
    "RequestsFacadeService",
    "SettingsReadService",
    "SetupFacadeService",
    "SimulationFacadeService",
    "TemplatePackFacadeService",
    "UserFacadeService",
]
