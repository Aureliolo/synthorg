"""Settings repository protocol."""

from collections.abc import Mapping, Sequence  # noqa: TC003
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE, IdKeyedRepository


class SettingRow(BaseModel):
    """Persistent settings record.

    Attributes:
        namespace: Setting namespace (part of composite primary key).
        key: Setting key within the namespace (part of composite primary key).
        value: Setting value as a string.
        updated_at: ISO 8601 timestamp of the last update.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    namespace: NotBlankStr = Field(description="Setting namespace")
    key: NotBlankStr = Field(description="Setting key")
    value: str = Field(description="Setting value")
    updated_at: str = Field(description="ISO 8601 timestamp")


SettingRowKey = tuple[NotBlankStr, NotBlankStr]
"""Composite primary key: ``(namespace, key)``."""


@runtime_checkable
class SettingsRepository(
    IdKeyedRepository[SettingRow, SettingRowKey],
    Protocol,
):
    """CRUD interface for namespaced settings persistence.

    Composes :class:`IdKeyedRepository` (ADR-0001) with composite key
    ``(namespace, key)`` per D8. Bespoke per D7: :meth:`get_namespace`,
    :meth:`set_many`, :meth:`delete_namespace`, and
    :meth:`delete_namespace_returning_keys` encode atomic multi-row and
    namespace-scoped operations that the generic surface cannot express.
    """

    async def save(self, entity: SettingRow) -> None:
        """Persist a setting (upsert by composite key).

        Args:
            entity: The setting to persist.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get(self, entity_id: SettingRowKey) -> SettingRow | None:
        """Retrieve a setting by composite key.

        Args:
            entity_id: ``(namespace, key)`` tuple.

        Returns:
            The setting, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[SettingRow, ...]:
        """List settings across all namespaces (paginated).

        Args:
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Paginated settings ordered by ``(namespace, key)`` ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete(self, entity_id: SettingRowKey) -> bool:
        """Delete a setting by composite key.

        Args:
            entity_id: ``(namespace, key)`` tuple.

        Returns:
            ``True`` if a setting was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_namespace(
        self,
        namespace: NotBlankStr,
    ) -> tuple[SettingRow, ...]:
        """Retrieve all settings in a namespace (bespoke per ADR-0001 D7).

        Args:
            namespace: Setting namespace.

        Returns:
            All settings in the namespace, sorted by key ascending.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def set_if_unchanged(
        self,
        entity: SettingRow,
        expected_updated_at: str | None = None,
    ) -> bool:
        """Upsert a setting with optional compare-and-swap (bespoke per D7).

        Args:
            entity: The setting to upsert.
            expected_updated_at: When provided, enforces atomic CAS -- the
                row is only updated if the current ``updated_at`` matches.
                Empty string ``""`` signals "only insert if no row exists".

        Returns:
            ``True`` if the write succeeded, ``False`` if the CAS condition
            was not met.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def set_many(
        self,
        items: Sequence[SettingRow],
        *,
        expected_updated_at_map: (Mapping[SettingRowKey, str] | None) = None,
    ) -> bool:
        """Atomically upsert multiple settings (bespoke per ADR-0001 D7).

        Each element of ``items`` is a ``SettingRow`` instance.
        ``expected_updated_at_map`` optionally supplies a
        compare-and-swap expected version per ``(namespace, key)``
        composite key; keys absent from the map are upserted
        unconditionally. Pass an empty string ``""`` in the map for
        first-write CAS semantics (the row must not exist yet).

        The whole operation is atomic: if any CAS check fails, the
        transaction rolls back and no rows are modified.

        Args:
            items: Settings to upsert.
            expected_updated_at_map: Optional CAS version map keyed by
                composite ``(namespace, key)``.

        Returns:
            ``True`` if every write succeeded. ``False`` if any CAS
            check failed; callers should re-read versions and retry
            if they need to recover.

        Raises:
            PersistenceError: On DB-level failures (not CAS misses).
        """
        ...

    async def delete_namespace(self, namespace: NotBlankStr) -> int:
        """Delete all settings in a namespace (bespoke per ADR-0001 D7).

        Args:
            namespace: Setting namespace.

        Returns:
            Number of settings deleted.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete_namespace_returning_keys(
        self,
        namespace: NotBlankStr,
    ) -> tuple[NotBlankStr, ...]:
        """Atomically delete namespace, returning deleted keys (bespoke per D7).

        Equivalent to :meth:`delete_namespace` but returns the keys
        whose rows were actually removed in a single transaction;
        callers (notably :class:`SettingsService.delete_namespace`)
        rely on this to scope per-key change-publish notifications to
        the subset that genuinely changed, without a TOCTOU
        ``get_namespace`` + ``delete_namespace`` race.

        Args:
            namespace: Setting namespace.

        Returns:
            Tuple of keys (within *namespace*) whose override row
            was removed by this call, in implementation-defined order.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...
