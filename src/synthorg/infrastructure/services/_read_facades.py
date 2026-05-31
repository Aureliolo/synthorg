# module-kind: service
# ruff: noqa: EM101, E501
"""Read-through facades over externally-owned infrastructure primitives.

These facades wrap an already-attached AppState primitive (settings,
provider registry, backup service, auth service) and surface a thin
capability-checked read/write surface to the MCP handler layer.

Primitives are stored internally as :class:`Any` so the facade can
introspect capabilities at runtime (``getattr`` + ``callable`` checks)
without fighting protocol-type narrowing when the primitive is still
evolving.

The file-level ``EM101`` / ``E501`` suppressions are intentional:
capability-gap messages are string literals passed straight to
:class:`CapabilityNotSupportedError`, and the long-form capability
descriptions read better on one line for grep-ability than broken
across multiple.
"""

import json
from typing import TYPE_CHECKING, Any, cast

from synthorg.communication.mcp_errors import CapabilityNotSupportedError
from synthorg.infrastructure.services._shared import (
    _require_callable,
    _split_setting_key,
)
from synthorg.observability import get_logger
from synthorg.observability.events.backup import (
    BACKUP_DELETED_VIA_MCP,
    BACKUP_RESTORE_TRIGGERED_VIA_MCP,
)
from synthorg.observability.events.settings import (
    SETTINGS_VALUE_DELETED,
    SETTINGS_VALUE_SET,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from synthorg.api.auth.service import AuthService
    from synthorg.backup.service import BackupService as CoreBackupService
    from synthorg.core.types import NotBlankStr
    from synthorg.providers.health import ProviderHealthTracker
    from synthorg.providers.management.service import ProviderManagementService
    from synthorg.providers.registry import ProviderRegistry
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


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
        """Run a connectivity test against ``provider_id`` and return its result.

        Returns:
            A mapping of ``provider_id`` to the connectivity test result.
        """
        fn = _require_callable(
            self._management,
            "test_provider",
            "provider_test",
            "ProviderManagementService does not expose test_provider",
        )
        return {"provider_id": provider_id, "result": await fn(provider_id)}


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
        """Fetch a single backup by id.

        Returns:
            The backup record for ``backup_id`` as returned by the
            underlying backup service.
        """
        return await self._service.get_backup(backup_id)

    async def create_backup(
        self,
        *,
        trigger: object,
        components: object = None,
    ) -> object:
        """Request a new backup for the given ``trigger``/``components``.

        Returns:
            The backup record created by the underlying backup service.
        """
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
        """Trigger a restore from ``backup_id`` and emit the audit event.

        Returns:
            A mapping confirming the restore: ``backup_id`` and
            ``restored=True``.
        """
        await self._service.restore_from_backup(backup_id)
        logger.info(
            BACKUP_RESTORE_TRIGGERED_VIA_MCP,
            backup_id=backup_id,
            actor_id=actor_id,
            reason=reason,
        )
        return {"backup_id": backup_id, "restored": True}


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
        """Capability gap -- user onboarding flow owns user creation.

        Raises:
            CapabilityNotSupportedError: Always; users are provisioned via
                the onboarding flow, not over MCP.
        """
        raise CapabilityNotSupportedError(
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
        """Capability gap -- auth controller owns user mutations.

        Raises:
            CapabilityNotSupportedError: Always; user mutations go through
                the auth controller, not over MCP.
        """
        raise CapabilityNotSupportedError(
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
        """Capability gap -- deletion flows through the operator workflow.

        Raises:
            CapabilityNotSupportedError: Always; user deletion is a
                protected operator workflow, not an MCP operation.
        """
        raise CapabilityNotSupportedError(
            "user_delete",
            "user deletion is a protected operator workflow",
        )
