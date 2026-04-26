"""Settings repository protocol."""

from collections.abc import Mapping, Sequence  # noqa: TC003
from typing import Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001


@runtime_checkable
class SettingsRepository(Protocol):
    """CRUD interface for namespaced settings persistence.

    Settings are stored as ``(namespace, key)`` composite-keyed
    string values with ``updated_at`` timestamps.
    """

    async def get(
        self,
        namespace: NotBlankStr,
        key: NotBlankStr,
    ) -> tuple[str, str] | None:
        """Retrieve a setting value and its timestamp.

        Args:
            namespace: Setting namespace.
            key: Setting key within the namespace.

        Returns:
            ``(value, updated_at)`` tuple, or ``None`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_namespace(
        self,
        namespace: NotBlankStr,
    ) -> tuple[tuple[NotBlankStr, str, str], ...]:
        """Retrieve all settings in a namespace.

        Args:
            namespace: Setting namespace.

        Returns:
            Tuple of ``(key, value, updated_at)`` tuples, sorted by key.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def get_all(
        self,
    ) -> tuple[tuple[NotBlankStr, NotBlankStr, str, str], ...]:
        """Retrieve all settings across all namespaces.

        Returns:
            Tuple of ``(namespace, key, value, updated_at)`` tuples,
            sorted by namespace then key.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def set(
        self,
        namespace: NotBlankStr,
        key: NotBlankStr,
        value: str,
        updated_at: str,
        *,
        expected_updated_at: str | None = None,
    ) -> bool:
        """Upsert a setting value.

        Args:
            namespace: Setting namespace.
            key: Setting key within the namespace.
            value: Setting value as a string.
            updated_at: ISO 8601 timestamp of the change.
            expected_updated_at: When provided, the row is only
                updated if the current ``updated_at`` matches
                (atomic compare-and-swap).

        Returns:
            ``True`` if the write succeeded.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def set_many(
        self,
        items: Sequence[tuple[NotBlankStr, NotBlankStr, str, str]],
        *,
        expected_updated_at_map: (
            Mapping[tuple[NotBlankStr, NotBlankStr], str] | None
        ) = None,
    ) -> bool:
        """Atomically upsert multiple settings in a single transaction.

        Each element of ``items`` is ``(namespace, key, value,
        updated_at)``.  ``expected_updated_at_map`` optionally supplies
        a compare-and-swap expected version per ``(namespace, key)``;
        keys absent from the map are upserted unconditionally.  Pass
        an empty string ``""`` in the map for first-write CAS
        semantics (the row must not exist yet).

        The whole operation is atomic: if any CAS check fails, the
        transaction rolls back and no rows are modified.

        Returns:
            ``True`` if every write succeeded.  ``False`` if any CAS
            check failed; callers should re-read versions and retry
            if they need to recover.

        Raises:
            PersistenceError: On DB-level failures (not CAS misses).
        """
        ...

    async def delete(
        self,
        namespace: NotBlankStr,
        key: NotBlankStr,
    ) -> bool:
        """Delete a setting.

        Args:
            namespace: Setting namespace.
            key: Setting key within the namespace.

        Returns:
            ``True`` if the setting was deleted, ``False`` if not found.

        Raises:
            PersistenceError: If the operation fails.
        """
        ...

    async def delete_namespace(self, namespace: NotBlankStr) -> int:
        """Delete all settings in a namespace.

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
        """Atomically delete all settings in a namespace, returning the keys.

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
